"""
Tests para el módulo de métricas de performance (Gini, KS).

El nuevo diseño requiere pasar metric_type y lag_semanas explícitamente.
execution_week = origination_week + lag_semanas (el bug anterior usaba origination_week para ambos).
"""

import pandas as pd
import pytest
from datetime import date

from mlmonitor.metrics.performance import compute_gini_ks, get_gini_ks_for_segment
from conftest import TARGET_NAME, TARGET_LAG, WEEK_0


# ---------------------------------------------------------------------------
# Unit tests — sin DB
# ---------------------------------------------------------------------------

class TestComputeGiniKS:
    def test_perfect_model_gini(self):
        """Modelo perfecto: todos los eventos en bins de score alto (invertido)."""
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
        result = compute_gini_ks(pd.DataFrame())
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
        assert abs(result["gini"] - round(2 * result["auc"] - 1, 4)) < 1e-4


# ---------------------------------------------------------------------------
# Integration tests — con DB
# ---------------------------------------------------------------------------

class TestGiniKSFromDB:
    def test_returns_dict_with_keys(self, session, segment_ids, score_week):
        """get_gini_ks_for_segment retorna dict con claves gini/ks/auc."""
        reg_id = segment_ids["s1"]
        result = get_gini_ks_for_segment(
            session, reg_id, score_week,
            metric_type=TARGET_NAME, lag_semanas=TARGET_LAG,
        )
        assert "gini" in result
        assert "ks" in result
        assert "auc" in result

    def test_gini_in_valid_range(self, session, segment_ids, score_week):
        """Gini debe estar entre -1 y 1."""
        for seg in ["s1", "s2"]:
            reg_id = segment_ids[seg]
            result = get_gini_ks_for_segment(
                session, reg_id, score_week,
                metric_type=TARGET_NAME, lag_semanas=TARGET_LAG,
            )
            if result["gini"] is not None:
                assert -1.0 <= result["gini"] <= 1.0, (
                    f"{seg} Gini fuera de rango: {result['gini']}"
                )

    def test_s1_has_positive_gini(self, session, segment_ids, score_week):
        """s1 tiene buena discriminación — Gini debe ser > 0."""
        reg_id = segment_ids["s1"]
        result = get_gini_ks_for_segment(
            session, reg_id, score_week,
            metric_type=TARGET_NAME, lag_semanas=TARGET_LAG,
        )
        assert result["gini"] is not None
        assert result["gini"] > 0.0, (
            f"s1 Gini esperado > 0, obtenido: {result['gini']}"
        )

    def test_correct_origination_week_finds_data(self, session, segment_ids):
        """
        Con origination_week correcto (WEEK_0) se encuentran datos individuales.
        Con origination_week futuro no hay datos → Gini es None.
        """
        reg_id = segment_ids["s1"]

        # origination_week donde hay datos (WEEK_0)
        result_correct = get_gini_ks_for_segment(
            session, reg_id, WEEK_0,
            metric_type=TARGET_NAME, lag_semanas=TARGET_LAG,
        )
        assert result_correct["gini"] is not None, (
            "Con origination_week correcto debe encontrar datos de performance"
        )

        # origination_week sin datos → None
        result_no_data = get_gini_ks_for_segment(
            session, reg_id, date(2099, 6, 1),
            metric_type=TARGET_NAME, lag_semanas=TARGET_LAG,
        )
        assert result_no_data["gini"] is None, (
            "Sin datos para esa semana, Gini debe ser None"
        )

    def test_future_week_returns_none(self, session, segment_ids):
        """Semana sin datos retorna None para todas las métricas."""
        reg_id = segment_ids["s1"]
        result = get_gini_ks_for_segment(
            session, reg_id, date(2099, 1, 1),
            metric_type=TARGET_NAME, lag_semanas=TARGET_LAG,
        )
        assert result["gini"] is None
        assert result["ks"] is None
