"""
Calculo de Gini y KS.

Fuente primaria: FACT_PERFORMANCE_INDIVIDUAL (datos a nivel de credito).
Fallback: FACT_PERFORMANCE_BINNED (datos agregados por decil).

IMPORTANTE: Score invertido para el calculo de curvas.
  inverted = 1000 - score
  (score bajo = alto riesgo -> al invertir, valor alto = alto riesgo = predictor ascendente)

Gini = 2 * AUC - 1  (regla trapezoidal)
KS = max diferencia entre distribuciones acumuladas de eventos y no-eventos
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from mlmonitor.db.models import FactPerformanceBinned, FactPerformanceIndividual


def _build_performance_df(
    session: Session,
    model_registry_id: int,
    origination_week: date,
    execution_week: date,
    metric_type: str = "first_payment_default2",
    score_max: int = 1000,
) -> pd.DataFrame:
    """Carga los outcomes de una semana y construye el DataFrame base."""
    rows = (
        session.query(FactPerformanceBinned)
        .filter(
            FactPerformanceBinned.model_registry_id == model_registry_id,
            FactPerformanceBinned.origination_week == origination_week,
            FactPerformanceBinned.execution_week == execution_week,
            FactPerformanceBinned.metric_type == metric_type,
        )
        .order_by(FactPerformanceBinned.score_midpoint)
        .all()
    )

    if not rows:
        return pd.DataFrame()

    data = []
    for r in rows:
        midpoint = r.score_midpoint or 0
        total = r.count_total or 0
        events = min(r.count_event_real or 0, total)
        non_events = total - events
        # Score invertido: score bajo (alto riesgo) → inverted alto (alto riesgo)
        inverted = score_max - midpoint

        data.append({
            "score_bin": r.score_bin,
            "score_midpoint": midpoint,
            "score_inverted": inverted,
            "count_total": total,
            "count_event": events,
            "count_non_event": non_events,
        })

    df = pd.DataFrame(data)
    # Ordenar por score_inverted descendente (más riesgoso primero)
    df = df.sort_values("score_inverted", ascending=False).reset_index(drop=True)
    return df


def compute_gini_ks(df: pd.DataFrame) -> dict[str, float]:
    """
    Calcula Gini y KS desde un DataFrame con columnas
    [count_event, count_non_event, count_total].
    Retorna {"gini": float, "ks": float, "auc": float}.
    """
    if df.empty:
        return {"gini": 0.0, "ks": 0.0, "auc": 0.5}

    total_events = df["count_event"].sum()
    total_non_events = df["count_non_event"].sum()

    if total_events == 0 or total_non_events == 0:
        return {"gini": 0.0, "ks": 0.0, "auc": 0.5}

    # Distribuciones acumuladas
    cum_events = df["count_event"].cumsum() / total_events
    cum_non_events = df["count_non_event"].cumsum() / total_non_events

    # KS = max diferencia
    ks = float((cum_events - cum_non_events).abs().max())

    # AUC usando regla trapezoidal (curva ROC: x=FPR, y=TPR)
    fpr = np.concatenate([[0], cum_non_events.values])
    tpr = np.concatenate([[0], cum_events.values])
    auc = float(np.trapz(tpr, fpr))

    gini = 2 * auc - 1

    return {"gini": round(gini, 4), "ks": round(ks, 4), "auc": round(auc, 4)}


def get_gini_ks_for_segment(
    session: Session,
    model_registry_id: int,
    origination_week: date,
    metric_type: str,
    lag_semanas: int,
    score_max: int = 1000,
) -> dict[str, float]:
    """
    Calcula Gini y KS para un submodelo.

    Usa datos individuales (FACT_PERFORMANCE_INDIVIDUAL) como fuente primaria.
    Si no hay datos individuales, hace fallback a datos binned.

    Args:
        model_registry_id: ID surrogado del registro del modelo (submodelo)
        origination_week: semana de surtimiento de la cohorte (disbursement week)
        metric_type: nombre del target (ej: 'b_malo8_13', 'first_payment_default2')
        lag_semanas: ventana de observacion del target
    """
    # Fuente primaria: datos individuales
    df_ind = _build_performance_df_individual(
        session, model_registry_id, origination_week, metric_type, score_max=score_max,
    )
    if not df_ind.empty:
        return compute_gini_ks_individual(df_ind)

    # Fallback: datos binned
    execution_week = origination_week + timedelta(weeks=lag_semanas)
    df = _build_performance_df(
        session, model_registry_id,
        origination_week=origination_week,
        execution_week=execution_week,
        metric_type=metric_type,
        score_max=score_max,
    )
    if df.empty:
        return {"gini": None, "ks": None, "auc": None}
    return compute_gini_ks(df)


# ---------------------------------------------------------------------------
# Individual-level Gini/KS (source: FACT_PERFORMANCE_INDIVIDUAL)
# ---------------------------------------------------------------------------


def _build_performance_df_individual(
    session: Session,
    model_registry_id: int,
    origination_week: date,
    metric_type: str,
    score_max: int = 1000,
) -> pd.DataFrame:
    """Carga scores y flags individuales de FACT_PERFORMANCE_INDIVIDUAL.

    Args:
        origination_week: semana de surtimiento (disbursement week) de la cohorte.
        metric_type: nombre del target (ventana).
    """
    rows = (
        session.query(FactPerformanceIndividual)
        .filter(
            FactPerformanceIndividual.model_registry_id == model_registry_id,
            FactPerformanceIndividual.origination_week == origination_week,
            FactPerformanceIndividual.ventana == metric_type,
        )
        .all()
    )

    if not rows:
        return pd.DataFrame()

    data = [{"fnpuntaje": r.fnpuntaje, "flag": r.flag} for r in rows]
    df = pd.DataFrame(data)
    # Invertir score: bajo score = alto riesgo -> inverted alto
    df["score_inverted"] = score_max - df["fnpuntaje"]
    return df.sort_values("score_inverted", ascending=False).reset_index(drop=True)


def compute_gini_ks_individual(df: pd.DataFrame) -> dict[str, float]:
    """Gini/KS desde datos individuales (mas preciso que bins).

    Cada fila es un credito con fnpuntaje y flag (0/1).
    El DataFrame debe estar ordenado por score_inverted descendente.
    """
    if df.empty:
        return {"gini": 0.0, "ks": 0.0, "auc": 0.5}

    total_events = df["flag"].sum()
    total_non_events = len(df) - total_events

    if total_events == 0 or total_non_events == 0:
        return {"gini": 0.0, "ks": 0.0, "auc": 0.5}

    # Distribuciones acumuladas a nivel individual
    cum_events = df["flag"].cumsum() / total_events
    cum_non_events = (1 - df["flag"]).cumsum() / total_non_events

    # KS = max diferencia
    ks = float((cum_events - cum_non_events).abs().max())

    # AUC usando regla trapezoidal (curva ROC: x=FPR, y=TPR)
    fpr = np.concatenate([[0], cum_non_events.values])
    tpr = np.concatenate([[0], cum_events.values])
    auc = float(np.trapz(tpr, fpr))

    gini = 2 * auc - 1

    return {"gini": round(gini, 4), "ks": round(ks, 4), "auc": round(auc, 4)}
