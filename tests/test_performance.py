"""Tests para el módulo de métricas de performance (Gini, KS)."""

import pandas as pd
import pytest

from mlmonitor.metrics.performance import compute_gini_ks, get_gini_ks_for_segment


class TestComputeGiniKS:
    def test_perfect_model_gini(self):
        """Modelo perfecto: todos los eventos en bins de score alto (invertido)."""
        # Con score invertido, los primeros registros son los de mayor riesgo
        df = pd.DataFrame({
            "count_event": [100, 50, 10, 0, 0, 0, 0, 0, 0, 0],
            "count_non_event": [0, 0, 0, 0, 0, 50, 100, 100, 100, 100],
            "count_total": [100, 50, 10, 0, 0, 50, 100, 100, 100, 100],
        })
        result = compute_gini_ks(df)
        assert result["gini"] > 0.8, f"Gini esperado > 0.8, obtenido: {result['gini']}"

    def test_random_model_gini_near_zero(self):
        """Modelo aleatorio: Gini debe ser cercano a 0."""
        df = pd.DataFrame({
            "count_event": [50] * 10,
            "count_non_event": [50] * 10,
            "count_total": [100] * 10,
        })
        result = compute_gini_ks(df)
        assert abs(result["gini"]) < 0.1, f"Gini esperado ~0, obtenido: {result['gini']}"

    def test_empty_df_returns_defaults(self):
        """DataFrame vacío retorna valores por defecto."""
        df = pd.DataFrame()
        result = compute_gini_ks(df)
        assert result["gini"] == 0.0
        assert result["ks"] == 0.0
        assert result["auc"] == 0.5

    def test_no_events_returns_defaults(self):
        """Sin eventos, no se puede calcular Gini/KS."""
        df = pd.DataFrame({
            "count_event": [0] * 5,
            "count_non_event": [100] * 5,
            "count_total": [100] * 5,
        })
        result = compute_gini_ks(df)
        assert result["gini"] == 0.0

    def test_ks_between_zero_and_one(self):
        """KS debe estar entre 0 y 1."""
        df = pd.DataFrame({
            "count_event": [80, 60, 40, 20, 10, 5, 3, 2, 1, 0],
            "count_non_event": [0, 5, 15, 30, 40, 50, 60, 70, 80, 100],
            "count_total": [80, 65, 55, 50, 50, 55, 63, 72, 81, 100],
        })
        result = compute_gini_ks(df)
        assert 0.0 <= result["ks"] <= 1.0, f"KS fuera de rango: {result['ks']}"

    def test_gini_and_auc_relationship(self):
        """Verificar que Gini = 2*AUC - 1."""
        df = pd.DataFrame({
            "count_event": [80, 50, 30, 15, 10, 5, 3, 2, 1, 0],
            "count_non_event": [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
            "count_total": [90, 70, 60, 55, 60, 65, 73, 82, 91, 100],
        })
        result = compute_gini_ks(df)
        expected_gini = round(2 * result["auc"] - 1, 4)
        assert abs(result["gini"] - expected_gini) < 1e-4


class TestGiniKSFromDB:
    def test_returns_dict_with_keys(self, session, model_id, performance_week):
        """get_gini_ks_for_segment retorna dict con claves gini/ks/auc."""
        result = get_gini_ks_for_segment(session, model_id, "s2", performance_week)
        assert "gini" in result
        assert "ks" in result
        assert "auc" in result

    def test_gini_in_valid_range(self, session, model_id, performance_week):
        """Gini debe estar entre -1 y 1."""
        for seg in ["s1", "s2", "s3", "s4", "s5"]:
            result = get_gini_ks_for_segment(session, model_id, seg, performance_week)
            if result["gini"] is not None:
                assert -1.0 <= result["gini"] <= 1.0, (
                    f"{seg} Gini fuera de rango: {result['gini']}"
                )

    def test_s1_gini_drops_over_weeks(self, session, model_id):
        """s1 debe tener Gini más bajo en semana 8 que en semana 1 (anomalía inyectada)."""
        from mlmonitor.data.dummy_generator import _week_date
        week1 = _week_date(1)
        week8 = _week_date(8)

        r1 = get_gini_ks_for_segment(session, model_id, "s1", week1)
        r8 = get_gini_ks_for_segment(session, model_id, "s1", week8)

        if r1["gini"] is not None and r8["gini"] is not None:
            assert r8["gini"] < r1["gini"], (
                f"s1 Gini semana8 ({r8['gini']:.4f}) debería ser menor "
                f"que semana1 ({r1['gini']:.4f})"
            )

    def test_missing_week_returns_none(self, session, model_id):
        """Semana sin datos retorna None para todas las métricas."""
        from datetime import date
        future_week = date(2030, 1, 1)
        result = get_gini_ks_for_segment(session, model_id, "s1", future_week)
        assert result["gini"] is None
        assert result["ks"] is None
