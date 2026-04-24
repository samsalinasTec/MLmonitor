"""
Tests para FACT_PERFORMANCE_INDIVIDUAL.

Verifica que:
- Los registros se insertan correctamente
- origination_week y execution_week son Date (no Integer)
- flag es siempre 0 o 1, nunca null
- No hay duplicados (credito_id, model_registry_id, ventana)
- execution_week - origination_week corresponde al lag en semanas
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date

import pytest
from mlmonitor.db.models import FactPerformanceIndividual
from conftest import TARGET_NAME, TARGET_LAG, WEEK_0


class TestFactPerformanceIndividual:
    def test_records_inserted(self, session, segment_ids):
        """Debe haber registros en FACT_PERFORMANCE_INDIVIDUAL."""
        reg_ids = list(segment_ids.values())
        count = (
            session.query(FactPerformanceIndividual)
            .filter(FactPerformanceIndividual.model_registry_id.in_(reg_ids))
            .count()
        )
        assert count > 0

    def test_origination_week_is_date(self, session, segment_ids):
        """origination_week debe ser Date, no Integer."""
        reg_ids = list(segment_ids.values())
        row = (
            session.query(FactPerformanceIndividual)
            .filter(FactPerformanceIndividual.model_registry_id.in_(reg_ids))
            .first()
        )
        assert row is not None
        assert isinstance(row.origination_week, date), (
            f"origination_week debe ser date, es {type(row.origination_week)}"
        )

    def test_flag_is_zero_or_one(self, session, segment_ids):
        """flag siempre es 0 o 1, nunca null."""
        reg_ids = list(segment_ids.values())
        rows = (
            session.query(FactPerformanceIndividual)
            .filter(FactPerformanceIndividual.model_registry_id.in_(reg_ids))
            .all()
        )
        assert all(r.flag in (0, 1) for r in rows)
        assert all(r.flag is not None for r in rows)

    def test_unique_per_credit_and_ventana(self, session, segment_ids):
        """No debe haber duplicados (credito_id, model_registry_id, ventana)."""
        reg_ids = list(segment_ids.values())
        total = (
            session.query(FactPerformanceIndividual)
            .filter(FactPerformanceIndividual.model_registry_id.in_(reg_ids))
            .count()
        )
        unique = (
            session.query(
                FactPerformanceIndividual.credito_id,
                FactPerformanceIndividual.model_registry_id,
                FactPerformanceIndividual.ventana,
            )
            .filter(FactPerformanceIndividual.model_registry_id.in_(reg_ids))
            .distinct()
            .count()
        )
        assert total == unique, f"Hay duplicados: {total} filas, {unique} unicas"

    def test_execution_week_is_origination_plus_lag(self, session, segment_ids):
        """execution_week - origination_week debe corresponder al lag (en semanas)."""
        reg_ids = list(segment_ids.values())
        rows = (
            session.query(FactPerformanceIndividual)
            .filter(
                FactPerformanceIndividual.model_registry_id.in_(reg_ids),
                FactPerformanceIndividual.ventana == TARGET_NAME,
            )
            .all()
        )
        for r in rows:
            delta_weeks = (r.execution_week - r.origination_week).days // 7
            assert delta_weeks == TARGET_LAG, (
                f"execution_week - origination_week = {delta_weeks} semanas, "
                f"esperado: {TARGET_LAG}"
            )

    def test_ventana_column_matches_target_name(self, session, segment_ids):
        """ventana debe ser el nombre de la variable target."""
        reg_ids = list(segment_ids.values())
        ventanas = (
            session.query(FactPerformanceIndividual.ventana)
            .filter(FactPerformanceIndividual.model_registry_id.in_(reg_ids))
            .distinct()
            .all()
        )
        ventana_names = {v[0] for v in ventanas}
        assert TARGET_NAME in ventana_names, (
            f"ventana '{TARGET_NAME}' no encontrada. Ventanas presentes: {ventana_names}"
        )

    def test_fnpuntaje_is_not_null(self, session, segment_ids):
        """fnpuntaje debe estar presente (no null) para los registros de prueba."""
        reg_ids = list(segment_ids.values())
        rows = (
            session.query(FactPerformanceIndividual)
            .filter(FactPerformanceIndividual.model_registry_id.in_(reg_ids))
            .all()
        )
        assert all(r.fnpuntaje is not None for r in rows), (
            "fnpuntaje no debe ser null en los datos de prueba"
        )
