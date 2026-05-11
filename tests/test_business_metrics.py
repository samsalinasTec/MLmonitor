"""
Tests para la tabla de métricas de negocio (heatmap por bin del scorecard).

Las violaciones de orden migraron a deciles reales del score continuo —
ver tests/test_decile_ordering.py.
"""

from mlmonitor.metrics.business_metrics import get_business_metrics_table
from conftest import TARGET_NAME


class TestBusinessMetricsTable:
    def test_returns_dataframe(self, session, segment_ids, current_week):
        """get_business_metrics_table retorna DataFrame no vacío."""
        reg_id = segment_ids["s1"]
        df = get_business_metrics_table(session, reg_id, current_week)
        assert not df.empty
        assert "score_bin" in df.columns
        assert "score_midpoint" in df.columns

    def test_columns_based_on_targets_in_db(self, session, segment_ids, current_week):
        """Las columnas de tasas se generan dinámicamente desde META_VARIABLES targets."""
        reg_id = segment_ids["s1"]
        df = get_business_metrics_table(session, reg_id, current_week)
        rate_col = f"{TARGET_NAME}_rate"
        assert rate_col in df.columns, (
            f"Se esperaba columna '{rate_col}' en {list(df.columns)}"
        )

    def test_rate_values_in_range(self, session, segment_ids, current_week):
        """Las tasas de evento deben estar entre 0 y 1."""
        reg_id = segment_ids["s1"]
        df = get_business_metrics_table(session, reg_id, current_week)
        rate_col = f"{TARGET_NAME}_rate"
        if rate_col in df.columns:
            valid = df[rate_col].dropna()
            assert (valid.between(0, 1)).all(), f"Tasas fuera de rango: {valid.tolist()}"

    def test_10_bins(self, session, segment_ids, current_week):
        """Deben existir exactamente 10 bins de score."""
        reg_id = segment_ids["s1"]
        df = get_business_metrics_table(session, reg_id, current_week)
        assert len(df) == 10, f"Esperados 10 bins, obtenidos: {len(df)}"

    def test_empty_when_no_targets(self, session, segment_ids, current_week):
        """Si no hay targets en META_VARIABLES para el segmento, retorna DataFrame vacío.
        (Testea el nuevo comportamiento genérico — no hay fallback hardcodeado)
        """
        df = get_business_metrics_table(session, -999, current_week)
        assert df.empty

    def test_no_data_for_future_week_returns_empty(self, session, segment_ids):
        """Semana sin datos retorna DataFrame vacío."""
        from datetime import date
        reg_id = segment_ids["s1"]
        df = get_business_metrics_table(session, reg_id, date(2099, 1, 1))
        assert df.empty
