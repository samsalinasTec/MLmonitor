"""
Tests para el módulo de cálculo de PSI.

Verifica que:
- PSI se calcule correctamente desde datos puros (unit tests)
- PSI use los bins fijos de META_VARIABLES.binning_rules (no recalcula desde datos actuales)
- s1 (distribución estable) → PSI ≈ 0
- s2 (distribución drifteada) → PSI CRITICAL > 0.20
- null_rate de s2 en semana actual > umbral CRITICAL
"""

import pandas as pd
import pytest

from mlmonitor.metrics.psi import (
    compute_psi_from_df,
    get_max_psi,
    get_null_rates,
    get_psi_for_all_variables,
    get_psi_for_variable,
)


# ---------------------------------------------------------------------------
# Unit tests — sin DB
# ---------------------------------------------------------------------------

class TestComputePSI:
    def test_identical_distributions_returns_zero(self):
        """PSI = 0 cuando las distribuciones son idénticas."""
        df = pd.DataFrame({
            "bin_label": [f"bin_{i}" for i in range(10)],
            "bin_percentage": [0.1] * 10,
        })
        assert abs(compute_psi_from_df(df, df.copy())) < 1e-6

    def test_large_drift_returns_high_psi(self):
        """PSI alto cuando hay gran deriva en la distribución."""
        ref = pd.DataFrame({
            "bin_label": ["low", "mid", "high"],
            "bin_percentage": [0.70, 0.20, 0.10],
        })
        cur = pd.DataFrame({
            "bin_label": ["low", "mid", "high"],
            "bin_percentage": [0.10, 0.20, 0.70],
        })
        assert compute_psi_from_df(ref, cur) > 0.20

    def test_small_drift_returns_low_psi(self):
        """PSI bajo cuando hay poca diferencia entre distribuciones."""
        ref = pd.DataFrame({
            "bin_label": [f"b{i}" for i in range(5)],
            "bin_percentage": [0.20, 0.22, 0.18, 0.21, 0.19],
        })
        cur = pd.DataFrame({
            "bin_label": [f"b{i}" for i in range(5)],
            "bin_percentage": [0.19, 0.21, 0.20, 0.22, 0.18],
        })
        assert compute_psi_from_df(ref, cur) < 0.10

    def test_empty_df_returns_zero(self):
        ref = pd.DataFrame(columns=["bin_label", "bin_percentage"])
        cur = pd.DataFrame(columns=["bin_label", "bin_percentage"])
        assert compute_psi_from_df(ref, cur) == 0.0

    def test_psi_is_non_negative(self):
        """PSI siempre debe ser >= 0."""
        import random
        rng = random.Random(42)
        bins = [f"b{i}" for i in range(8)]

        for _ in range(20):
            ref_probs = [rng.uniform(0.05, 0.30) for _ in bins]
            s = sum(ref_probs)
            ref_probs = [p / s for p in ref_probs]

            cur_probs = [rng.uniform(0.05, 0.30) for _ in bins]
            s = sum(cur_probs)
            cur_probs = [p / s for p in cur_probs]

            ref = pd.DataFrame({"bin_label": bins, "bin_percentage": ref_probs})
            cur = pd.DataFrame({"bin_label": bins, "bin_percentage": cur_probs})

            assert compute_psi_from_df(ref, cur) >= 0.0


# ---------------------------------------------------------------------------
# Integration tests — con DB
# ---------------------------------------------------------------------------

class TestPSIFromDB:
    def test_psi_returns_float(self, session, segment_ids, variable_ids, current_week):
        """PSI calculado desde DB retorna un float válido."""
        reg_id = segment_ids["s1"]
        var_id = variable_ids["s1"]["num_var"]
        psi = get_psi_for_variable(session, reg_id, var_id, current_week)
        assert isinstance(psi, float)
        assert psi >= 0.0

    def test_s1_stable_distribution_psi_near_zero(
        self, session, segment_ids, variable_ids, current_week
    ):
        """s1 tiene distribución estable → PSI ≈ 0 (< 0.10)."""
        reg_id = segment_ids["s1"]
        var_id = variable_ids["s1"]["num_var"]
        psi = get_psi_for_variable(session, reg_id, var_id, current_week)
        assert psi < 0.10, (
            f"s1 num_var debe tener PSI < 0.10 (estable), obtenido: {psi:.4f}"
        )

    def test_s2_drifted_distribution_psi_critical(
        self, session, segment_ids, variable_ids, current_week
    ):
        """s2 tiene distribución drifteada → PSI CRITICAL (> 0.20)."""
        reg_id = segment_ids["s2"]
        var_id = variable_ids["s2"]["num_var"]
        psi = get_psi_for_variable(session, reg_id, var_id, current_week)
        assert psi > 0.20, (
            f"s2 num_var debe tener PSI > 0.20 (CRITICAL), obtenido: {psi:.4f}"
        )

    def test_all_variables_returns_dict(
        self, session, segment_ids, variable_ids, current_week
    ):
        """get_psi_for_all_variables retorna dict con todas las variables."""
        reg_id = segment_ids["s1"]
        # Solo pasar variables no-target (que tienen distribuciones)
        var_map = {
            vid: vname
            for vname, vid in variable_ids["s1"].items()
            if vname in ("num_var", "cat_var")
        }
        psi_dict = get_psi_for_all_variables(session, reg_id, var_map, current_week)
        assert isinstance(psi_dict, dict)
        assert len(psi_dict) > 0
        for var, val in psi_dict.items():
            assert isinstance(val, float), f"PSI de {var} no es float: {val}"
            assert val >= 0.0

    def test_max_psi_returns_variable_with_highest_psi(
        self, session, segment_ids, variable_ids, current_week
    ):
        """get_max_psi retorna el PSI máximo y la variable correcta."""
        reg_id = segment_ids["s2"]
        var_map = {
            vid: vname
            for vname, vid in variable_ids["s2"].items()
            if vname in ("num_var", "cat_var")
        }
        psi_dict = get_psi_for_all_variables(session, reg_id, var_map, current_week)
        max_psi, max_var = get_max_psi(psi_dict)
        assert max_psi >= 0.0
        assert max_var in psi_dict
        assert psi_dict[max_var] == max_psi
        # s2 max psi debe ser num_var (la variable drifteada)
        assert max_var == "num_var", f"Esperado max_var='num_var', obtenido: '{max_var}'"

    def test_s2_null_rate_critical(
        self, session, segment_ids, variable_ids, current_week
    ):
        """s2 num_var tiene null_count alto en semana actual → null_rate CRITICAL (> 10%)."""
        reg_id = segment_ids["s2"]
        var_map = {
            vid: vname
            for vname, vid in variable_ids["s2"].items()
            if vname in ("num_var", "cat_var")
        }
        null_rates = get_null_rates(session, reg_id, var_map, current_week)
        assert "num_var" in null_rates
        assert null_rates["num_var"] > 0.10, (
            f"s2 num_var null_rate esperado > 10% (CRITICAL), "
            f"obtenido: {null_rates['num_var']:.2%}"
        )

    def test_s1_null_rate_is_zero(
        self, session, segment_ids, variable_ids, current_week
    ):
        """s1 no tiene nulls → null_rate = 0."""
        reg_id = segment_ids["s1"]
        var_map = {
            vid: vname
            for vname, vid in variable_ids["s1"].items()
            if vname in ("num_var", "cat_var")
        }
        null_rates = get_null_rates(session, reg_id, var_map, current_week)
        if "num_var" in null_rates:
            assert null_rates["num_var"] == 0.0, (
                f"s1 no debe tener nulls, obtenido: {null_rates['num_var']:.2%}"
            )
