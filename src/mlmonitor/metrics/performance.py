"""
Cálculo de Gini y KS desde FACT_PERFORMANCE_OUTCOMES.

IMPORTANTE: Score invertido para el cálculo de curvas.
  inverted = 1000 - score_midpoint
  (score bajo = alto riesgo → al invertir, valor alto = alto riesgo = predictor ascendente)

Gini = 2 × AUC - 1  (regla trapezoidal)
KS = max diferencia entre distribuciones acumuladas de eventos y no-eventos
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from mlmonitor.db.models import FactPerformanceOutcomes


def _build_performance_df(
    session: Session,
    model_registry_id: int,
    date_score_key: date,
    date_outcome_key: date,
    metric_type: str = "roll_forward",
) -> pd.DataFrame:
    """Carga los outcomes de una semana y construye el DataFrame base."""
    rows = (
        session.query(FactPerformanceOutcomes)
        .filter(
            FactPerformanceOutcomes.model_registry_id == model_registry_id,
            FactPerformanceOutcomes.date_score_key == date_score_key,
            FactPerformanceOutcomes.date_outcome_key == date_outcome_key,
            FactPerformanceOutcomes.metric_type == metric_type,
        )
        .order_by(FactPerformanceOutcomes.score_midpoint)
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
        inverted = 1000 - midpoint

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
    performance_week: date,
    lag_weeks: int = 8,
) -> dict[str, float]:
    """
    Calcula Gini y KS para un segmento en la semana de performance.

    Args:
        model_registry_id: ID surrogado del registro del modelo (segmento)
        performance_week: date_score_key (semana en que se generó el score)
        lag_weeks: semanas de lag para el outcome (default 8)
    """
    date_outcome_key = performance_week + timedelta(weeks=lag_weeks)
    df = _build_performance_df(
        session, model_registry_id,
        date_score_key=performance_week,
        date_outcome_key=date_outcome_key,
        metric_type="roll_forward",
    )
    if df.empty:
        return {"gini": None, "ks": None, "auc": None}
    return compute_gini_ks(df)
