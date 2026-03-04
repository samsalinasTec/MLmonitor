"""Tests para métricas de negocio: RollForward y PaymentRate ordering."""

import pandas as pd
import pytest

from mlmonitor.metrics.business_metrics import (
    check_ordering_violations,
    get_business_metrics_table,
    get_payment_rate_violations,
    get_roll_forward_violations,
)


class TestCheckOrderingViolations:
    def _make_df(self, roll_forward_values, payment_rate_values=None):
        n = len(roll_forward_values)
        return pd.DataFrame({
            "score_bin": [f"{i*100}-{(i+1)*100}" for i in range(n)],
            "score_midpoint": [i * 100 + 50 for i in range(n)],
            "roll_forward_rate": roll_forward_values,
            "payment_rate": payment_rate_values or [0.1 * (i + 1) for i in range(n)],
        })

    def test_monotone_decreasing_no_violations(self):
        """RollForward monotóno decreciente = sin violaciones."""
        df = self._make_df([0.70, 0.60, 0.50, 0.40, 0.30, 0.20, 0.15, 0.10, 0.07, 0.05])
        result = check_ordering_violations(df, "roll_forward_rate", ascending=False)
        assert result["violations"] == 0

    def test_monotone_increasing_no_violations_payment(self):
        """PaymentRate monotóno creciente = sin violaciones."""
        df = self._make_df(
            [0.70, 0.60, 0.50, 0.40, 0.30, 0.20, 0.15, 0.10, 0.07, 0.05],
            payment_rate_values=[0.05, 0.10, 0.20, 0.30, 0.40, 0.55, 0.65, 0.75, 0.85, 0.90],
        )
        result = check_ordering_violations(df, "payment_rate", ascending=True)
        assert result["violations"] == 0

    def test_inverted_two_bins_gives_violation(self):
        """Inversión entre bins 3 y 4 = 1 violación."""
        df = self._make_df([0.70, 0.60, 0.40, 0.50, 0.30, 0.20, 0.15, 0.10, 0.07, 0.05])
        result = check_ordering_violations(df, "roll_forward_rate", ascending=False)
        assert result["violations"] == 1
        assert len(result["violation_pairs"]) == 1

    def test_completely_inverted_returns_many_violations(self):
        """Distribución completamente invertida = múltiples violaciones."""
        df = self._make_df([0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90])
        result = check_ordering_violations(df, "roll_forward_rate", ascending=False)
        assert result["violations"] >= 5

    def test_tolerance_prevents_false_positives(self):
        """Pequeñas diferencias (< 0.005) no deben contar como violaciones."""
        df = self._make_df([0.70, 0.70, 0.70, 0.70, 0.70, 0.70, 0.70, 0.70, 0.70, 0.70])
        result = check_ordering_violations(df, "roll_forward_rate", ascending=False)
        assert result["violations"] == 0

    def test_violation_pairs_contain_correct_info(self):
        """violation_pairs debe tener info de los bins involucrados."""
        df = self._make_df([0.60, 0.70, 0.50, 0.40, 0.30, 0.20, 0.15, 0.10, 0.07, 0.05])
        result = check_ordering_violations(df, "roll_forward_rate", ascending=False)
        assert result["violations"] == 1
        pair = result["violation_pairs"][0]
        assert "bin_low" in pair
        assert "bin_high" in pair
        assert "value_low_score_bin" in pair
        assert "value_high_score_bin" in pair


class TestS4Violations:
    def test_s4_roll_forward_violations_weeks_7_8(self, session, segment_ids):
        """s4 debe tener violaciones de ordering en semanas 7-8 (anomalía inyectada)."""
        from mlmonitor.data.dummy_generator import _week_date
        reg_id = segment_ids["s4"]

        for week in [7, 8]:
            week_date = _week_date(week)
            result = get_roll_forward_violations(session, reg_id, week_date)
            assert result["violations"] >= 1, (
                f"s4 semana {week}: esperadas >= 1 violaciones, "
                f"obtenidas: {result['violations']}"
            )

    def test_s4_normal_weeks_no_violations(self, session, segment_ids):
        """s4 en semanas sin anomalías debe tener 0 violaciones (o pocas)."""
        from mlmonitor.data.dummy_generator import _week_date
        week3 = _week_date(3)
        reg_id = segment_ids["s4"]
        result = get_roll_forward_violations(session, reg_id, week3)
        # En semanas sin anomalías el ordering debería respetarse
        assert result["violations"] <= 1, (
            f"s4 semana 3: demasiadas violaciones inesperadas: {result['violations']}"
        )


class TestBusinessMetricsTable:
    def test_returns_dataframe(self, session, segment_ids, performance_week):
        """get_business_metrics_table retorna DataFrame válido."""
        reg_id = segment_ids["s1"]
        df = get_business_metrics_table(session, reg_id, performance_week)
        assert not df.empty
        assert "score_bin" in df.columns
        assert "roll_forward_rate" in df.columns
        assert "payment_rate" in df.columns

    def test_10_bins(self, session, segment_ids, performance_week):
        """Deben existir exactamente 10 bins de score."""
        reg_id = segment_ids["s1"]
        df = get_business_metrics_table(session, reg_id, performance_week)
        assert len(df) == 10, f"Esperados 10 bins, obtenidos: {len(df)}"

    def test_roll_forward_values_in_range(self, session, segment_ids, performance_week):
        """Tasa de deterioro debe estar entre 0 y 1."""
        reg_id = segment_ids["s2"]
        df = get_business_metrics_table(session, reg_id, performance_week)
        if not df.empty:
            assert (df["roll_forward_rate"].between(0, 1)).all()

    def test_payment_rate_values_in_range(self, session, segment_ids, performance_week):
        """Tasa de pago debe estar entre 0 y 1."""
        reg_id = segment_ids["s2"]
        df = get_business_metrics_table(session, reg_id, performance_week)
        if not df.empty:
            assert (df["payment_rate"].between(0, 1)).all()
