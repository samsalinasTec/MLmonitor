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
    def test_psi_returns_float(self, session, model_id, current_week):
        """PSI calculado desde DB retorna un float válido."""
        psi = get_psi_for_variable(
            session, model_id, "G1", "dias_atraso", current_week
        )
        assert isinstance(psi, float)
        assert psi >= 0.0

    def test_all_variables_returns_dict(self, session, model_id, current_week):
        """get_psi_for_all_variables retorna dict con todas las variables."""
        psi_dict = get_psi_for_all_variables(
            session, model_id, "G1", current_week
        )
        assert isinstance(psi_dict, dict)
        assert len(psi_dict) > 0
        for var, val in psi_dict.items():
            assert isinstance(val, float), f"PSI de {var} no es float: {val}"
            assert val >= 0.0

    def test_g3_dias_atraso_psi_is_critical(self, session, model_id, current_week):
        """G3 debe tener PSI CRITICAL en dias_atraso (anomalía inyectada semana 17-20)."""
        psi = get_psi_for_variable(
            session, model_id, "G3", "dias_atraso", current_week
        )
        assert psi > 0.20, (
            f"G3 dias_atraso esperado PSI > 0.20 (CRITICAL), obtenido: {psi:.4f}"
        )

    def test_s3_saldo_deuda_psi_is_warning(self, session, model_id):
        """S3 debe tener PSI WARNING en saldo_deuda (anomalía semana 15-20)."""
        from mlmonitor.data.dummy_generator import _week_date
        week_20 = _week_date(20)
        psi = get_psi_for_variable(
            session, model_id, "S3", "saldo_deuda", week_20
        )
        assert psi > 0.10, (
            f"S3 saldo_deuda esperado PSI > 0.10 (WARNING), obtenido: {psi:.4f}"
        )

    def test_max_psi_returns_correct_variable(self, session, model_id, current_week):
        """get_max_psi retorna el PSI máximo y la variable correcta."""
        psi_dict = get_psi_for_all_variables(
            session, model_id, "G3", current_week
        )
        max_psi, max_var = get_max_psi(psi_dict)
        assert max_psi >= 0.0
        assert max_var in psi_dict
        assert psi_dict[max_var] == max_psi

    def test_null_rates_returns_dict(self, session, model_id, current_week):
        """get_null_rates retorna dict con tasas de nulos."""
        null_rates = get_null_rates(session, model_id, "S12", current_week)
        assert isinstance(null_rates, dict)
        # S12 debe tener null rate alto en historial_pagos (anomalía semana 18-20)
        if "historial_pagos" in null_rates:
            assert null_rates["historial_pagos"] > 0.05, (
                f"S12 historial_pagos null rate esperado > 5%, "
                f"obtenido: {null_rates['historial_pagos']:.2%}"
            )
