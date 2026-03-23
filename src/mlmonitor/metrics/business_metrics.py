"""
Métricas de negocio: tasas de target por decil de score.

Reglas de ordenamiento:
- Tasas de malo (incumplimiento): deben DECRECER conforme sube el score
  (bajo score = alto riesgo)
- Tasas de pago: deben CRECER conforme sube el score

La dirección esperada se lee de MetaVariables.ascending_order por target.

check_ordering_violations(): cuenta el número de bins consecutivos donde
la métrica viola la monotonía esperada.

Tasas se calculan al vuelo: count_event_real / count_total por metric_type.
"""

from datetime import date

import pandas as pd
from sqlalchemy.orm import Session

from mlmonitor.db.models import FactPerformanceOutcomes, MetaVariables


def get_business_metrics_table(
    session: Session,
    model_registry_id: int,
    score_week: date,
) -> pd.DataFrame:
    """
    Retorna tabla de métricas de negocio por decil ordenada por score ascendente.
    Los targets y sus columnas se determinan dinámicamente desde META_VARIABLES.

    Args:
        model_registry_id: ID surrogado del registro del modelo (segmento)
        score_week: semana en que se generó el score (date_score_key)

    Returns:
        DataFrame con columnas: score_bin, score_midpoint, count_total,
        {target_name}_rate por cada variable target activa.
    """
    targets = (
        session.query(MetaVariables)
        .filter(
            MetaVariables.model_registry_id == model_registry_id,
            MetaVariables.variable_rol == "target",
            MetaVariables.valid_to.is_(None),
        )
        .all()
    )

    if not targets:
        return pd.DataFrame()

    target_names = [t.variable_name for t in targets]

    rows = (
        session.query(FactPerformanceOutcomes)
        .filter(
            FactPerformanceOutcomes.model_registry_id == model_registry_id,
            FactPerformanceOutcomes.date_score_key == score_week,
            FactPerformanceOutcomes.metric_type.in_(target_names),
        )
        .order_by(FactPerformanceOutcomes.score_midpoint)
        .all()
    )

    if not rows:
        return pd.DataFrame()

    by_bin: dict[str, dict] = {}
    for r in rows:
        key = r.score_bin
        if key not in by_bin:
            by_bin[key] = {
                "score_bin": r.score_bin,
                "score_midpoint": r.score_midpoint or 0,
                "count_total": r.count_total or 0,
            }
            for tname in target_names:
                by_bin[key][f"{tname}_rate"] = None

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
        ascending: True si la métrica debe crecer con el score
                   False si debe decrecer (b_malo)

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
            # FirstPaymentDefault2 debe crecer: v_j >= v_i
            if v_j < v_i - 0.005:
                violations += 1
                violation_pairs.append({
                    "bin_low": bins[i],
                    "bin_high": bins[i + 1],
                    "value_low_score_bin": round(v_i, 4),
                    "value_high_score_bin": round(v_j, 4),
                })
        else:
            #  b_malo debe decrecer: v_j <= v_i
            if v_j > v_i + 0.005:
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
    score_week: date,
    metric_type: str,
    ascending: bool = False,
) -> dict:
    """
    Verifica si una variable target viola la monotonía esperada a lo largo de los bins.

    Args:
        score_week: date_score_key (semana en que se generó el score)
        metric_type: nombre del target (ej: 'b_malo8_13', 'first_payment_default2')
        ascending: True si debe crecer con score, False si debe decrecer (default)

    Returns:
        dict con violations y violation_pairs
    """
    df = get_business_metrics_table(session, model_registry_id, score_week)
    col = f"{metric_type}_rate"
    if df.empty or col not in df.columns:
        return {"violations": 0, "violation_pairs": []}
    return check_ordering_violations(df, col, ascending=ascending)
