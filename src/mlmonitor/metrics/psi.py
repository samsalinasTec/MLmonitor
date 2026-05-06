"""
Cálculo de PSI (Population Stability Index) y null_rate con ventana rodante.

Referencia: META_BASELINE_DISTRIBUTIONS (baseline de entrenamiento).
Actual:     FACT_DISTRIBUTIONS agregado sobre una ventana rodante de
            `PSI_WINDOW_WEEKS` semanas (current_week + las N-1 anteriores).

PSI = Σ (P_actual - P_ref) × ln(P_actual / P_ref)

Umbrales:
  < 0.10  → OK
  0.10-0.20 → WARNING
  > 0.20  → CRITICAL

Por qué ventana rodante: una sola semana de producción tiene varianza alta
(efectos de calendario, volumen bajo, ruido idiosincrático). Sumar `bin_count`
crudo sobre 4 semanas equivale a calcular PSI sobre la población combinada de
ese mes y suaviza el ruido sin sesgo. Ver `docs/decisions.md §8.2.27`.

Cobertura parcial: si la ventana cubre menos semanas (al inicio del histórico
o si hay huecos), se usa lo que existe en `FACT_DISTRIBUTIONS`. No se rellena
con ceros.
"""

import math
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import func
from sqlalchemy.orm import Session

from mlmonitor.db.models import FactDistributions, MetaBaselineDistributions

EPS = 1e-8  # evitar log(0)
PSI_WINDOW_WEEKS = 4  # ventana rodante: current_week + las 3 semanas anteriores


def _window_weeks(current_week: date, window_weeks: int = PSI_WINDOW_WEEKS) -> list[date]:
    """Devuelve la lista de lunes ISO en la ventana, en orden descendente."""
    return [current_week - timedelta(weeks=i) for i in range(window_weeks)]


def compute_psi_from_df(ref_df: pd.DataFrame, cur_df: pd.DataFrame) -> float:
    """
    Calcula PSI a partir de dos DataFrames con columnas [bin_label, bin_percentage].
    Los bins se alinean por bin_label.
    """
    merged = ref_df.merge(cur_df, on="bin_label", suffixes=("_ref", "_cur"))
    if merged.empty:
        return 0.0

    p_ref = merged["bin_percentage_ref"].clip(lower=EPS)
    p_cur = merged["bin_percentage_cur"].clip(lower=EPS)

    # Renormalizar
    p_ref = p_ref / p_ref.sum()
    p_cur = p_cur / p_cur.sum()

    psi = ((p_cur - p_ref) * (p_cur / p_ref).apply(math.log)).sum()
    return float(psi)


def _aggregate_distributions_over_window(
    session: Session,
    model_registry_id: int,
    variable_id: int,
    current_week: date,
    window_weeks: int = PSI_WINDOW_WEEKS,
) -> pd.DataFrame:
    """
    Suma `bin_count` por `bin_label` sobre la ventana rodante y devuelve un
    DataFrame [bin_label, bin_percentage] renormalizado.

    Si no hay datos en ninguna semana de la ventana, devuelve un DataFrame vacío.
    """
    weeks = _window_weeks(current_week, window_weeks)

    rows = (
        session.query(
            FactDistributions.bin_label,
            func.sum(FactDistributions.bin_count).label("bin_count"),
        )
        .filter(
            FactDistributions.model_registry_id == model_registry_id,
            FactDistributions.variable_id == variable_id,
            FactDistributions.origination_week.in_(weeks),
        )
        .group_by(FactDistributions.bin_label)
        .all()
    )

    if not rows:
        return pd.DataFrame(columns=["bin_label", "bin_percentage"])

    df = pd.DataFrame(
        [{"bin_label": r.bin_label, "bin_count": int(r.bin_count or 0)} for r in rows]
    )
    total = df["bin_count"].sum()
    if total <= 0:
        return pd.DataFrame(columns=["bin_label", "bin_percentage"])

    df["bin_percentage"] = df["bin_count"] / total
    return df[["bin_label", "bin_percentage"]]


def get_psi_for_variable(
    session: Session,
    model_registry_id: int,
    variable_id: int,
    current_week: date,
    window_weeks: int = PSI_WINDOW_WEEKS,
) -> float:
    """
    Calcula PSI comparando la distribución actual (agregada sobre la ventana
    rodante de `window_weeks` semanas) con el baseline de entrenamiento.
    """
    ref_rows = (
        session.query(MetaBaselineDistributions)
        .filter(
            MetaBaselineDistributions.model_registry_id == model_registry_id,
            MetaBaselineDistributions.variable_id == variable_id,
        )
        .all()
    )
    if not ref_rows:
        return 0.0

    cur_df = _aggregate_distributions_over_window(
        session, model_registry_id, variable_id, current_week, window_weeks
    )
    if cur_df.empty:
        return 0.0

    ref_df = pd.DataFrame(
        [{"bin_label": r.bin_label, "bin_percentage": r.bin_percentage or 0.0}
         for r in ref_rows]
    )

    return compute_psi_from_df(ref_df, cur_df)


def get_psi_for_all_variables(
    session: Session,
    model_registry_id: int,
    variable_map: dict[int, str],
    current_week: date,
    window_weeks: int = PSI_WINDOW_WEEKS,
) -> dict[str, float]:
    """
    Calcula PSI para todas las variables de un segmento usando ventana rodante.

    Args:
        model_registry_id: ID surrogado del registro del modelo (segmento)
        variable_map: {variable_id: variable_name}
        current_week: semana de cálculo (último lunes ISO de la ventana)
        window_weeks: tamaño de la ventana rodante (default 4)

    Returns:
        {variable_name: psi_value}
    """
    result = {}
    for var_id, var_name in variable_map.items():
        result[var_name] = get_psi_for_variable(
            session, model_registry_id, var_id, current_week, window_weeks
        )
    return result


def get_max_psi(psi_by_variable: dict[str, float]) -> tuple[float, str]:
    """Retorna (max_psi, variable_name) del PSI más alto entre todas las variables."""
    if not psi_by_variable:
        return 0.0, ""
    max_var = max(psi_by_variable, key=lambda k: psi_by_variable[k])
    return psi_by_variable[max_var], max_var


def get_null_rates(
    session: Session,
    model_registry_id: int,
    variable_map: dict[int, str],
    current_week: date,
    window_weeks: int = PSI_WINDOW_WEEKS,
) -> dict[str, float]:
    """
    Calcula la tasa de nulos por variable agregada sobre la ventana rodante.

    null_rate = Σ null_count / Σ total_records sobre las semanas presentes en
    la ventana. La agregación se hace por (variable_id, bin_label) y luego se
    consolida — `total_records` se cuenta una sola vez por (variable, semana)
    porque `FactDistributions` lo replica en cada bin de la misma semana.

    Args:
        model_registry_id: ID surrogado del registro del modelo (segmento)
        variable_map: {variable_id: variable_name}
        current_week: semana de cálculo (último lunes ISO de la ventana)
        window_weeks: tamaño de la ventana rodante (default 4)

    Returns:
        {variable_name: null_rate}
    """
    weeks = _window_weeks(current_week, window_weeks)

    # null_count se suma sobre todos los bins; total_records se duplica por bin
    # en cada semana, así que lo agregamos por semana (max) y luego sumamos.
    null_rows = (
        session.query(
            FactDistributions.variable_id,
            func.sum(FactDistributions.null_count).label("null_sum"),
        )
        .filter(
            FactDistributions.model_registry_id == model_registry_id,
            FactDistributions.origination_week.in_(weeks),
        )
        .group_by(FactDistributions.variable_id)
        .all()
    )

    total_rows = (
        session.query(
            FactDistributions.variable_id,
            FactDistributions.origination_week,
            func.max(FactDistributions.total_records).label("total_records"),
        )
        .filter(
            FactDistributions.model_registry_id == model_registry_id,
            FactDistributions.origination_week.in_(weeks),
        )
        .group_by(FactDistributions.variable_id, FactDistributions.origination_week)
        .all()
    )

    if not null_rows or not total_rows:
        return {}

    null_by_var: dict[int, int] = {r.variable_id: int(r.null_sum or 0) for r in null_rows}
    total_by_var: dict[int, int] = {}
    for r in total_rows:
        total_by_var[r.variable_id] = total_by_var.get(r.variable_id, 0) + int(r.total_records or 0)

    return {
        var_name: null_by_var.get(var_id, 0) / max(total_by_var.get(var_id, 0), 1)
        for var_id, var_name in variable_map.items()
        if var_id in total_by_var
    }
