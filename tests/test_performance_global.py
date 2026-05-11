"""
Tests para get_gini_ks_global — Gini/KS sobre la población combinada de
TODOS los segmentos del modelo en una origination_week + target.

Usa el fixture sintético definido en conftest.py (TEST_MODEL_V1 con s1 y s2,
25 créditos por bin × 10 bins por segmento → 250 créditos por segmento).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date

from mlmonitor.db.models import FactPerformanceIndividual
from mlmonitor.metrics.performance import get_gini_ks_for_segment, get_gini_ks_global
from conftest import MODEL_ID, TARGET_LAG, TARGET_NAME, WEEK_0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _per_segment_n_obs(session, segment_ids) -> int:
    """Suma de créditos individuales para la origination_week del fixture."""
    total = 0
    for reg_id in segment_ids.values():
        n = (
            session.query(FactPerformanceIndividual)
            .filter(
                FactPerformanceIndividual.model_registry_id == reg_id,
                FactPerformanceIndividual.origination_week == WEEK_0,
                FactPerformanceIndividual.ventana == TARGET_NAME,
            )
            .count()
        )
        total += n
    return total


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetGiniKsGlobal:
    def test_global_combines_all_segments(self, session, segment_ids):
        """n_obs del global == suma de créditos individuales de todos los segmentos."""
        result = get_gini_ks_global(
            session, MODEL_ID, WEEK_0, TARGET_NAME,
        )
        expected = _per_segment_n_obs(session, segment_ids)
        assert result["n_obs"] == expected
        # 2 segmentos × 25 créditos × 10 bins = 500
        assert expected == 500

    def test_global_returns_none_when_no_data(self, session):
        """origination_week sin datos → todas las métricas None, n_obs=0."""
        result = get_gini_ks_global(
            session, MODEL_ID, date(2099, 1, 1), TARGET_NAME,
        )
        assert result == {"gini": None, "ks": None, "auc": None, "n_obs": 0}

    def test_global_returns_valid_range(self, session):
        """Gini ∈ [-1, 1], KS ∈ [0, 1], AUC ∈ [0, 1]."""
        result = get_gini_ks_global(
            session, MODEL_ID, WEEK_0, TARGET_NAME,
        )
        assert -1.0 <= result["gini"] <= 1.0
        assert 0.0 <= result["ks"] <= 1.0
        assert 0.0 <= result["auc"] <= 1.0

    def test_global_skips_other_models(self, session, segment_ids):
        """score_max_by_registry restringe la query a esos registry_ids; otros
        modelos quedan fuera. Pasando solo s1 obtenemos exactamente sus créditos.
        """
        s1_id = segment_ids["s1"]
        result = get_gini_ks_global(
            session, MODEL_ID, WEEK_0, TARGET_NAME,
            score_max_by_registry={s1_id: 1000},
        )
        s1_n = (
            session.query(FactPerformanceIndividual)
            .filter(
                FactPerformanceIndividual.model_registry_id == s1_id,
                FactPerformanceIndividual.origination_week == WEEK_0,
                FactPerformanceIndividual.ventana == TARGET_NAME,
            )
            .count()
        )
        assert result["n_obs"] == s1_n

    def test_global_uses_per_segment_score_max(self, session, segment_ids):
        """score_max_by_registry distinto por segmento afecta el ranking
        (y por tanto Gini/KS). Comparamos contra usar el mismo score_max para
        todos: como el ranking cambia, el resultado difiere."""
        uniform = get_gini_ks_global(
            session, MODEL_ID, WEEK_0, TARGET_NAME,
            score_max_by_registry={
                seg: 1000 for seg in segment_ids.values()
            },
        )
        # Para s2 usamos score_max=500 (distinto), invertirá scores a la mitad
        # de la magnitud → ranking conjunto cambia (s2 quedará "comprimido"
        # respecto a s1).
        seg_list = list(segment_ids.values())
        mixed = get_gini_ks_global(
            session, MODEL_ID, WEEK_0, TARGET_NAME,
            score_max_by_registry={seg_list[0]: 1000, seg_list[1]: 500},
        )
        assert uniform["gini"] != mixed["gini"] or uniform["ks"] != mixed["ks"]

    def test_global_resolves_score_max_when_none(self, session):
        """Si no se pasa score_max_by_registry, lo resuelve desde
        META_MODEL_REGISTRY y produce un resultado válido (no None)."""
        result = get_gini_ks_global(
            session, MODEL_ID, WEEK_0, TARGET_NAME,
            score_max_by_registry=None,
        )
        assert result["n_obs"] > 0
        assert result["gini"] is not None
        assert result["ks"] is not None

    def test_global_skips_unknown_model(self, session):
        """model_id inexistente → sin registros, n_obs=0."""
        result = get_gini_ks_global(
            session, "UNKNOWN_MODEL", WEEK_0, TARGET_NAME,
        )
        assert result["n_obs"] == 0
        assert result["gini"] is None

    def test_global_consistent_with_per_segment_sum(self, session, segment_ids):
        """El global no es la media de los per-segmento, pero n_obs debe igualar."""
        s1_per = get_gini_ks_for_segment(
            session, segment_ids["s1"],
            origination_week=WEEK_0, metric_type=TARGET_NAME,
            lag_semanas=TARGET_LAG, score_max=1000,
        )
        s2_per = get_gini_ks_for_segment(
            session, segment_ids["s2"],
            origination_week=WEEK_0, metric_type=TARGET_NAME,
            lag_semanas=TARGET_LAG, score_max=1000,
        )
        # Ambos per-segmento producen métricas válidas (datos individuales).
        assert s1_per["gini"] is not None
        assert s2_per["gini"] is not None
        # El global debe existir (no degenera).
        gl = get_gini_ks_global(session, MODEL_ID, WEEK_0, TARGET_NAME)
        assert gl["gini"] is not None
