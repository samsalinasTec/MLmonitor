"""Tests para el módulo de cálculo de PSI."""

import pandas as pd
import pytest

from mlmonitor.metrics.psi import (
    compute_psi_from_df,
    get_max_psi,
    get_null_rates,
    get_psi_for_all_variables,
    get_psi_for_variable,
)


class TestComputePSI:
    def test_identical_distributions_returns_zero(self):
        """PSI = 0 cuando las distribuciones son idénticas."""
        df = pd.DataFrame({
            "bin_label": [f"bin_{i}" for i in range(10)],
            "bin_percentage": [0.1] * 10,
        })
        psi = compute_psi_from_df(df, df.copy())
        assert abs(psi) < 1e-6

    def test_large_drift_returns_high_psi(self):
        """PSI alto cuando hay gran deriva en la distribución."""
        ref = pd.DataFrame({
            "bin_label": ["low", "mid", "high"],
            "bin_percentage": [0.70, 0.20, 0.10],
        })
        cur = pd.DataFrame({
            "bin_label": ["low", "mid", "high"],
            "bin_percentage": [0.10, 0.20, 0.70],  # distribución invertida
        })
        psi = compute_psi_from_df(ref, cur)
        assert psi > 0.20, f"PSI esperado > 0.20, obtenido: {psi}"

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
        psi = compute_psi_from_df(ref, cur)
        assert psi < 0.10, f"PSI esperado < 0.10, obtenido: {psi}"

    def test_empty_df_returns_zero(self):
        ref = pd.DataFrame(columns=["bin_label", "bin_percentage"])
        cur = pd.DataFrame(columns=["bin_label", "bin_percentage"])
        psi = compute_psi_from_df(ref, cur)
        assert psi == 0.0

    def test_psi_is_non_negative(self):
        """PSI siempre debe ser >= 0."""
        import random
        rng = random.Random(42)
        bins = [f"b{i}" for i in range(8)]

        for _ in range(20):
            ref_probs = [rng.uniform(0.05, 0.30) for _ in bins]
            total = sum(ref_probs)
            ref_probs = [p / total for p in ref_probs]

            cur_probs = [rng.uniform(0.05, 0.30) for _ in bins]
            total = sum(cur_probs)
            cur_probs = [p / total for p in cur_probs]

            ref = pd.DataFrame({"bin_label": bins, "bin_percentage": ref_probs})
            cur = pd.DataFrame({"bin_label": bins, "bin_percentage": cur_probs})

            psi = compute_psi_from_df(ref, cur)
            assert psi >= 0.0, f"PSI negativo: {psi}"


class TestPSIFromDB:
    def test_psi_returns_float(self, session, segment_ids, variable_ids, current_week):
        """PSI calculado desde DB retorna un float válido."""
        reg_id = segment_ids["s1"]
        var_id = variable_ids["s1"]["nivel_endeudamiento"]
        psi = get_psi_for_variable(session, reg_id, var_id, current_week)
        assert isinstance(psi, float)
        assert psi >= 0.0

    def test_all_variables_returns_dict(self, session, segment_ids, variable_ids, current_week):
        """get_psi_for_all_variables retorna dict con todas las variables."""
        reg_id = segment_ids["s1"]
        var_map = {v: k for k, v in variable_ids["s1"].items()}  # {var_id: var_name}
        psi_dict = get_psi_for_all_variables(session, reg_id, var_map, current_week)
        assert isinstance(psi_dict, dict)
        assert len(psi_dict) > 0
        for var, val in psi_dict.items():
            assert isinstance(val, float), f"PSI de {var} no es float: {val}"
            assert val >= 0.0

    def test_s3_nivel_endeudamiento_psi_is_critical(
        self, session, segment_ids, variable_ids, current_week
    ):
        """s3 debe tener PSI CRITICAL en nivel_endeudamiento (anomalía inyectada semana 17-20)."""
        reg_id = segment_ids["s3"]
        var_id = variable_ids["s3"]["nivel_endeudamiento"]
        psi = get_psi_for_variable(session, reg_id, var_id, current_week)
        assert psi > 0.20, (
            f"s3 nivel_endeudamiento esperado PSI > 0.20 (CRITICAL), obtenido: {psi:.4f}"
        )

    def test_s5_capacidad_pago_psi_is_warning(self, session, segment_ids, variable_ids):
        """s5 debe tener PSI WARNING en capacidad_pago (anomalía semana 15-20)."""
        from mlmonitor.data.dummy_generator import _week_date
        week_20 = _week_date(20)
        reg_id = segment_ids["s5"]
        var_id = variable_ids["s5"]["capacidad_pago"]
        psi = get_psi_for_variable(session, reg_id, var_id, week_20)
        assert psi > 0.10, (
            f"s5 capacidad_pago esperado PSI > 0.10 (WARNING), obtenido: {psi:.4f}"
        )

    def test_max_psi_returns_correct_variable(
        self, session, segment_ids, variable_ids, current_week
    ):
        """get_max_psi retorna el PSI máximo y la variable correcta."""
        reg_id = segment_ids["s3"]
        var_map = {v: k for k, v in variable_ids["s3"].items()}
        psi_dict = get_psi_for_all_variables(session, reg_id, var_map, current_week)
        max_psi, max_var = get_max_psi(psi_dict)
        assert max_psi >= 0.0
        assert max_var in psi_dict
        assert psi_dict[max_var] == max_psi

    def test_null_rates_returns_dict(self, session, segment_ids, variable_ids, current_week):
        """get_null_rates retorna dict con tasas de nulos."""
        reg_id = segment_ids["s9"]
        var_map = {v: k for k, v in variable_ids["s9"].items()}
        null_rates = get_null_rates(session, reg_id, var_map, current_week)
        assert isinstance(null_rates, dict)
        # s9 debe tener null rate alto en meses_en_buro (anomalía semana 18-20)
        if "meses_en_buro" in null_rates:
            assert null_rates["meses_en_buro"] > 0.05, (
                f"s9 meses_en_buro null rate esperado > 5%, "
                f"obtenido: {null_rates['meses_en_buro']:.2%}"
            )
