"""
Métricas de negocio: tasas B_MALO por decil de score.

Reglas de ordenamiento:
- Tasas de malo (incumplimiento): deben DECRECER conforme sube el score
  (bajo score = alto riesgo)

check_ordering_violations(): cuenta el número de bins consecutivos donde
la métrica viola la monotonía esperada.

Tasas se calculan al vuelo: count_event_real / count_total por metric_type.
"""

from datetime import date

import pandas as pd
from sqlalchemy.orm import Session

from mlmonitor.db.models import FactPerformanceOutcomes

# Variables de performance activas del scorecard BazBoost.
# b_malo14_26 y b_malo14_52 excluidas por inmadurez (0% de eventos en el CSV actual).
# Esta lista es la fuente canónica — importarla donde se necesite.
B_MALO_ACTIVE = ['b_malo2_4', 'b_malo4_6', 'b_malo8_13', 'b_malo8_16', 'first_payment_default2']
B_MALO_PRIMARY = 'first_payment_default2'


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
        lag_weeks: no usado (mantenido para compatibilidad de firma — datos pre-labeled)

    Returns:
        DataFrame con columnas: score_bin, score_midpoint, count_total,
        b_malo2_4_rate, b_malo4_6_rate, b_malo8_13_rate, b_malo8_16_rate,
        first_payment_default2_rate
    """
    rows = (
        session.query(FactPerformanceOutcomes)
        .filter(
            FactPerformanceOutcomes.model_registry_id == model_registry_id,
            FactPerformanceOutcomes.date_score_key == reference_week,
            FactPerformanceOutcomes.date_outcome_key == reference_week,
            FactPerformanceOutcomes.metric_type.in_(B_MALO_ACTIVE),
        )
        .order_by(FactPerformanceOutcomes.score_midpoint)
        .all()
    )

    if not rows:
        return pd.DataFrame()

    # Agrupar por score_bin y construir columnas por b_malo var
    by_bin: dict[str, dict] = {}
    for r in rows:
        key = r.score_bin
        if key not in by_bin:
            by_bin[key] = {
                "score_bin": r.score_bin,
                "score_midpoint": r.score_midpoint or 0,
                "count_total": r.count_total or 0,
            }
            for col in B_MALO_ACTIVE:
                by_bin[key][f"{col}_rate"] = None

        total = r.count_total or 0
        rate = (r.count_event_real / total) if total else None
        rate_col = f"{r.metric_type}_rate"
        if rate_col in by_bin[key]:
            by_bin[key][rate_col] = rate

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


def get_ordering_violations_for_metric(
    session: Session,
    model_registry_id: int,
    reference_week: date,
    metric_type: str,
) -> dict:
    """
    Verifica si una variable b_malo viola la monotonía esperada a lo largo de los bins.

    Las tasas de malo deben DECRECER conforme sube el score (bin bajo = mayor riesgo).

    Args:
        metric_type: nombre de la columna b_malo (ej: 'b_malo8_13', 'first_payment_default2')

    Returns:
        dict con violations y violation_pairs
    """
    df = get_business_metrics_table(session, model_registry_id, reference_week)
    col = f"{metric_type}_rate"
    if df.empty or col not in df.columns:
        return {"violations": 0, "violation_pairs": []}
    return check_ordering_violations(df, col, ascending=False)
