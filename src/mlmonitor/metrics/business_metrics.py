"""
Métricas de negocio: tasas de target por decil de score (heatmap del PDF).

Las violaciones de orden se calculan sobre deciles reales del score continuo
(ver decile_metrics.py), no sobre los bins fijos del scorecard. Esto módulo
solo expone la tabla agrupada por bin para el heatmap visual.

Tasas se calculan al vuelo: count_event_real / count_total por metric_type.
"""

from datetime import date, timedelta

import pandas as pd
from sqlalchemy.orm import Session

from mlmonitor.db.models import FactPerformanceBinned, MetaVariables


def get_business_metrics_table(
    session: Session,
    model_registry_id: int,
    calculation_week: date,
) -> pd.DataFrame:
    """
    Retorna tabla de métricas de negocio por decil ordenada por score ascendente.

    Cada target tiene un lag distinto, por lo que los datos de cada target viven
    en una origination_week diferente (= calculation_week - lag). Esta función
    computa el origination_week correcto por target.

    Args:
        model_registry_id: ID surrogado del registro del modelo (segmento)
        calculation_week: semana de cálculo/ejecución del pipeline

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

    by_bin: dict[str, dict] = {}

    for target in targets:
        tname = target.variable_name
        lag = target.lag_semanas or 0
        origination_week = calculation_week - timedelta(weeks=lag)

        rows = (
            session.query(FactPerformanceBinned)
            .filter(
                FactPerformanceBinned.model_registry_id == model_registry_id,
                FactPerformanceBinned.origination_week == origination_week,
                FactPerformanceBinned.metric_type == tname,
            )
            .order_by(FactPerformanceBinned.score_midpoint)
            .all()
        )

        for r in rows:
            key = r.score_bin
            if key not in by_bin:
                by_bin[key] = {
                    "score_bin": r.score_bin,
                    "score_midpoint": r.score_midpoint or 0,
                    "count_total": r.count_total or 0,
                }
                for tn in target_names:
                    by_bin[key][f"{tn}_rate"] = None

            total = r.count_total or 0
            rate = (r.count_event_real / total) if total else None
            rate_col = f"{tname}_rate"
            if rate_col in by_bin[key]:
                by_bin[key][rate_col] = rate

    if not by_bin:
        return pd.DataFrame()

    data = list(by_bin.values())
    df = pd.DataFrame(data).sort_values("score_midpoint").reset_index(drop=True)

    # Heatmap: por cada columna {target}_rate, calcular {target}_color con
    # gradiente azul slate (rgba) normalizado al min/max de la columna. Color
    # neutral — solo comunica concentración, sin valencia bueno/malo.
    for tname in target_names:
        rate_col = f"{tname}_rate"
        color_col = f"{tname}_color"
        if rate_col not in df.columns:
            df[color_col] = None
            continue
        values = pd.to_numeric(df[rate_col], errors="coerce")
        v_min = values.min(skipna=True)
        v_max = values.max(skipna=True)
        if pd.isna(v_min) or pd.isna(v_max) or v_max == v_min:
            df[color_col] = [None] * len(df)
            continue
        rng = v_max - v_min
        colors: list[str | None] = []
        for v in values:
            if pd.isna(v):
                colors.append(None)
                continue
            norm = (v - v_min) / rng
            alpha = 0.05 + norm * 0.80
            colors.append(f"rgba(71, 85, 105, {alpha:.3f})")
        df[color_col] = colors

    return df
