"""
Métricas de negocio: RollForward y PaymentRate por decil de score.

Reglas de ordenamiento:
- RollForward: debe DECRECER conforme sube el score (bajo score = alto riesgo)
- PaymentRate: debe CRECER conforme sube el score

check_ordering_violation(): cuenta el número de bins consecutivos donde
la métrica viola la monotonía esperada.

Tasas se calculan al vuelo: count_event_real / count_total por metric_type.
"""

from datetime import date, timedelta

import pandas as pd
from sqlalchemy.orm import Session

from mlmonitor.db.models import FactPerformanceOutcomes


def get_business_metrics_table(
    session: Session,
    model_registry_id: int,
    reference_week: date,
    lag_weeks: int = 8,
) -> pd.DataFrame:
    """
    Retorna tabla de métricas de negocio por decil ordenada por score ascendente.

    Args:
        model_registry_id: ID surrogado del registro del modelo (segmento)
        reference_week: semana en que se generó el score (date_score_key)
        lag_weeks: semanas de lag para el outcome

    Returns:
        DataFrame con columnas: score_bin, score_midpoint, count_total,
        roll_forward_rate, payment_rate
    """
    outcome_week = reference_week + timedelta(weeks=lag_weeks)
    rows = (
        session.query(FactPerformanceOutcomes)
        .filter(
            FactPerformanceOutcomes.model_registry_id == model_registry_id,
            FactPerformanceOutcomes.date_score_key == reference_week,
            FactPerformanceOutcomes.date_outcome_key == outcome_week,
            FactPerformanceOutcomes.metric_type.in_(["roll_forward", "payment_rate_50"]),
        )
        .order_by(FactPerformanceOutcomes.score_midpoint)
        .all()
    )

    if not rows:
        return pd.DataFrame()

    # Agrupar por score_bin y calcular tasas desde conteos atómicos
    by_bin: dict[str, dict] = {}
    for r in rows:
        key = r.score_bin
        if key not in by_bin:
            by_bin[key] = {
                "score_bin": r.score_bin,
                "score_midpoint": r.score_midpoint or 0,
                "count_total": r.count_total or 0,
                "roll_forward_rate": None,
                "payment_rate": None,
            }
        total = r.count_total or 0
        rate = (r.count_event_real / total) if total else None
        if r.metric_type == "roll_forward":
            by_bin[key]["roll_forward_rate"] = rate
        elif r.metric_type == "payment_rate_50":
            by_bin[key]["payment_rate"] = rate

    data = list(by_bin.values())
    return pd.DataFrame(data).sort_values("score_midpoint").reset_index(drop=True)


def check_ordering_violations(
    df: pd.DataFrame,
    metric_col: str,
    ascending: bool,
) -> dict:
    """
    Verifica monotonía de una métrica a lo largo de los bins (ordenados por score asc).

    Args:
        df: DataFrame con score_midpoint y la métrica
        metric_col: nombre de la columna de la métrica
        ascending: True si la métrica debe crecer con el score (PaymentRate)
                   False si debe decrecer (RollForward)

    Returns:
        dict con:
          - violations: número de violaciones
          - violation_pairs: lista de (bin_i, bin_j, value_i, value_j)
    """
    df_sorted = df.sort_values("score_midpoint").reset_index(drop=True)
    values = df_sorted[metric_col].tolist()
    bins = df_sorted["score_bin"].tolist()

    violations = 0
    violation_pairs = []

    for i in range(len(values) - 1):
        v_i = values[i]
        v_j = values[i + 1]
        if v_i is None or v_j is None:
            continue

        if ascending:
            # PaymentRate debe crecer: v_j >= v_i
            if v_j < v_i - 0.005:  # tolerancia pequeña
                violations += 1
                violation_pairs.append({
                    "bin_low": bins[i],
                    "bin_high": bins[i + 1],
                    "value_low_score_bin": round(v_i, 4),
                    "value_high_score_bin": round(v_j, 4),
                })
        else:
            # RollForward debe decrecer: v_j <= v_i
            if v_j > v_i + 0.005:  # tolerancia pequeña
                violations += 1
                violation_pairs.append({
                    "bin_low": bins[i],
                    "bin_high": bins[i + 1],
                    "value_low_score_bin": round(v_i, 4),
                    "value_high_score_bin": round(v_j, 4),
                })

    return {"violations": violations, "violation_pairs": violation_pairs}


def get_roll_forward_violations(
    session: Session,
    model_registry_id: int,
    reference_week: date,
) -> dict:
    df = get_business_metrics_table(session, model_registry_id, reference_week)
    if df.empty or "roll_forward_rate" not in df.columns:
        return {"violations": 0, "violation_pairs": []}
    return check_ordering_violations(df, "roll_forward_rate", ascending=False)


def get_payment_rate_violations(
    session: Session,
    model_registry_id: int,
    reference_week: date,
) -> dict:
    df = get_business_metrics_table(session, model_registry_id, reference_week)
    if df.empty or "payment_rate" not in df.columns:
        return {"violations": 0, "violation_pairs": []}
    return check_ordering_violations(df, "payment_rate", ascending=True)
