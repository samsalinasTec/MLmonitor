"""
Cálculo de PSI (Population Stability Index) desde FACT_DISTRIBUTIONS.

PSI = Σ (P_actual - P_ref) × ln(P_actual / P_ref)

Umbrales:
  < 0.10  → OK
  0.10-0.20 → WARNING
  > 0.20  → CRITICAL
"""

import math
from datetime import date

import pandas as pd
from sqlalchemy.orm import Session

from mlmonitor.db.models import FactDistributions

EPS = 1e-8  # evitar log(0)


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


def get_psi_for_variable(
    session: Session,
    model_registry_id: int,
    variable_id: int,
    current_week: date,
) -> float:
    """
    Calcula PSI comparando la distribución actual con la referencia de entrenamiento.
    """
    # Cargar referencia (reference_flag=1)
    ref_rows = (
        session.query(FactDistributions)
        .filter(
            FactDistributions.model_registry_id == model_registry_id,
            FactDistributions.variable_id == variable_id,
            FactDistributions.reference_flag == 1,
        )
        .all()
    )

    # Cargar distribución actual
    cur_rows = (
        session.query(FactDistributions)
        .filter(
            FactDistributions.model_registry_id == model_registry_id,
            FactDistributions.variable_id == variable_id,
            FactDistributions.reference_week == current_week,
            FactDistributions.reference_flag == 0,
        )
        .all()
    )

    if not ref_rows or not cur_rows:
        return 0.0

    ref_df = pd.DataFrame(
        [{"bin_label": r.bin_label, "bin_percentage": r.bin_percentage or 0.0}
         for r in ref_rows]
    )
    cur_df = pd.DataFrame(
        [{"bin_label": r.bin_label, "bin_percentage": r.bin_percentage or 0.0}
         for r in cur_rows]
    )

    return compute_psi_from_df(ref_df, cur_df)


def get_psi_for_all_variables(
    session: Session,
    model_registry_id: int,
    variable_map: dict[int, str],
    current_week: date,
) -> dict[str, float]:
    """
    Calcula PSI para todas las variables de un segmento.

    Args:
        model_registry_id: ID surrogado del registro del modelo (segmento)
        variable_map: {variable_id: variable_name}
        current_week: semana de cálculo

    Returns:
        {variable_name: psi_value}
    """
    result = {}
    for var_id, var_name in variable_map.items():
        result[var_name] = get_psi_for_variable(
            session, model_registry_id, var_id, current_week
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
) -> dict[str, float]:
    """
    Calcula la tasa de nulos por variable en la semana actual.

    Args:
        model_registry_id: ID surrogado del registro del modelo (segmento)
        variable_map: {variable_id: variable_name}
        current_week: semana de cálculo

    Returns:
        {variable_name: null_rate}
    """
    rows = (
        session.query(FactDistributions)
        .filter(
            FactDistributions.model_registry_id == model_registry_id,
            FactDistributions.reference_week == current_week,
            FactDistributions.reference_flag == 0,
        )
        .all()
    )

    if not rows:
        return {}

    # Invertir variable_map para lookup por ID
    id_to_name = variable_map  # ya es {var_id: var_name}

    by_variable: dict[str, dict] = {}
    for r in rows:
        vname = id_to_name.get(r.variable_id)
        if vname is None:
            continue
        if vname not in by_variable:
            by_variable[vname] = {
                "null_count": 0,
                "total_records": r.total_records or 1,
            }
        by_variable[vname]["null_count"] += r.null_count or 0

    return {
        vname: data["null_count"] / max(data["total_records"], 1)
        for vname, data in by_variable.items()
    }
