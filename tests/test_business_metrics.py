"""
Tests para métricas de negocio: tabla de tasas por decil y detección de violaciones
de ordenamiento.

El sistema es genérico: los targets se leen desde META_VARIABLES, no de constantes.
"""

import pandas as pd
import pytest

from mlmonitor.metrics.business_metrics import (
    check_ordering_violations,
    get_business_metrics_table,
    get_ordering_violations_for_metric,
)
from conftest import TARGET_NAME, TARGET_LAG


# ---------------------------------------------------------------------------
# Unit tests — sin DB, DataFrames sintéticos
# ---------------------------------------------------------------------------

class TestCheckOrderingViolations:
    def _make_df(self, values, ascending=False):
        n = len(values)
        col = "metric_col"
        return pd.DataFrame({
            "score_bin": [f"{i*100}-{(i+1)*100}" for i in range(n)],
            "score_midpoint": [i * 100 + 50 for i in range(n)],
            col: values,
        }), col

    def test_monotone_decreasing_no_violations(self):
        """Tasa de malo decreciente = 0 violaciones."""
        df, col = self._make_df([0.70, 0.60, 0.50, 0.40, 0.30, 0.20, 0.15, 0.10, 0.07, 0.05])
        result = check_ordering_violations(df, col, ascending=False)
        assert result["violations"] == 0

    def test_monotone_increasing_no_violations(self):
        """Tasa de pago creciente = 0 violaciones."""
        df, col = self._make_df([0.05, 0.10, 0.20, 0.30, 0.40, 0.55, 0.65, 0.75, 0.85, 0.90])
        result = check_ordering_violations(df, col, ascending=True)
        assert result["violations"] == 0

    def test_inverted_two_bins_gives_one_violation(self):
        """Inversión entre bins 3 y 4 = 1 violación."""
        df, col = self._make_df([0.70, 0.60, 0.40, 0.50, 0.30, 0.20, 0.15, 0.10, 0.07, 0.05])
        result = check_ordering_violations(df, col, ascending=False)
        assert result["violations"] == 1
        assert len(result["violation_pairs"]) == 1

    def test_completely_inverted_returns_many_violations(self):
        """Distribución invertida = múltiples violaciones."""
        df, col = self._make_df([0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90])
        result = check_ordering_violations(df, col, ascending=False)
        assert result["violations"] >= 5

    def test_tolerance_prevents_false_positives(self):
        """Diferencias < 0.005 no cuentan como violaciones."""
        df, col = self._make_df([0.70] * 10)
        result = check_ordering_violations(df, col, ascending=False)
        assert result["violations"] == 0

    def test_violation_pairs_contain_correct_info(self):
        """violation_pairs debe incluir info de bins y valores."""
        df, col = self._make_df([0.60, 0.70, 0.50, 0.40, 0.30, 0.20, 0.15, 0.10, 0.07, 0.05])
        result = check_ordering_violations(df, col, ascending=False)
        assert result["violations"] == 1
        pair = result["violation_pairs"][0]
        assert "bin_low" in pair
        assert "bin_high" in pair
        assert "value_low_score_bin" in pair
        assert "value_high_score_bin" in pair

    def test_none_values_skipped(self):
        """Bins con None no generan violación."""
        df, col = self._make_df([0.70, None, 0.50, 0.40, 0.30, 0.20, 0.15, 0.10, 0.07, 0.05])
        result = check_ordering_violations(df, col, ascending=False)
        assert result["violations"] == 0


# ---------------------------------------------------------------------------
# Integration tests — con DB
# ---------------------------------------------------------------------------

class TestBusinessMetricsTable:
    def test_returns_dataframe(self, session, segment_ids, score_week):
        """get_business_metrics_table retorna DataFrame no vacío."""
        reg_id = segment_ids["s1"]
        df = get_business_metrics_table(session, reg_id, score_week)
        assert not df.empty
        assert "score_bin" in df.columns
        assert "score_midpoint" in df.columns

    def test_columns_based_on_targets_in_db(self, session, segment_ids, score_week):
        """Las columnas de tasas se generan dinámicamente desde META_VARIABLES targets."""
        reg_id = segment_ids["s1"]
        df = get_business_metrics_table(session, reg_id, score_week)
        rate_col = f"{TARGET_NAME}_rate"
        assert rate_col in df.columns, (
            f"Se esperaba columna '{rate_col}' en {list(df.columns)}"
        )

    def test_rate_values_in_range(self, session, segment_ids, score_week):
        """Las tasas de evento deben estar entre 0 y 1."""
        reg_id = segment_ids["s1"]
        df = get_business_metrics_table(session, reg_id, score_week)
        rate_col = f"{TARGET_NAME}_rate"
        if rate_col in df.columns:
            valid = df[rate_col].dropna()
            assert (valid.between(0, 1)).all(), f"Tasas fuera de rango: {valid.tolist()}"

    def test_10_bins(self, session, segment_ids, score_week):
        """Deben existir exactamente 10 bins de score."""
        reg_id = segment_ids["s1"]
        df = get_business_metrics_table(session, reg_id, score_week)
        assert len(df) == 10, f"Esperados 10 bins, obtenidos: {len(df)}"

    def test_empty_when_no_targets(self, session, segment_ids, score_week):
        """Si no hay targets en META_VARIABLES para el segmento, retorna DataFrame vacío.
        (Testea el nuevo comportamiento genérico — no hay fallback hardcodeado)
        """
        # Este test verifica que el modelo sin targets da vacío, no errores
        df = get_business_metrics_table(session, -999, score_week)
        assert df.empty

    def test_no_data_for_future_week_returns_empty(self, session, segment_ids):
        """Semana sin datos retorna DataFrame vacío."""
        from datetime import date
        reg_id = segment_ids["s1"]
        df = get_business_metrics_table(session, reg_id, date(2099, 1, 1))
        assert df.empty


class TestOrderingViolations:
    def test_s2_has_ordering_violations(self, session, segment_ids, score_week):
        """s2 tiene inversión entre bins 3 y 4 → ≥ 1 violación."""
        reg_id = segment_ids["s2"]
        result = get_ordering_violations_for_metric(
            session, reg_id, score_week,
            metric_type=TARGET_NAME,
            ascending=False,
        )
        assert result["violations"] >= 1, (
            f"s2 debe tener ≥ 1 violación de ordering, obtenidas: {result['violations']}"
        )

    def test_s1_has_no_violations(self, session, segment_ids, score_week):
        """s1 tiene distribución monotona → 0 violaciones."""
        reg_id = segment_ids["s1"]
        result = get_ordering_violations_for_metric(
            session, reg_id, score_week,
            metric_type=TARGET_NAME,
            ascending=False,
        )
        assert result["violations"] == 0, (
            f"s1 no debe tener violaciones, obtenidas: {result['violations']}"
        )

    def test_result_has_required_keys(self, session, segment_ids, score_week):
        """El resultado incluye 'violations' y 'violation_pairs'."""
        reg_id = segment_ids["s1"]
        result = get_ordering_violations_for_metric(
            session, reg_id, score_week,
            metric_type=TARGET_NAME,
            ascending=False,
        )
        assert "violations" in result
        assert "violation_pairs" in result
        assert isinstance(result["violations"], int)
        assert isinstance(result["violation_pairs"], list)
