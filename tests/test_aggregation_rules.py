"""Tests del módulo `data/aggregation_rules.py` (Iteración 2 A3).

Cubre:
- Carga global cuando no hay override por modelo.
- Override por modelo_registry_id que gana sobre global.
- Versionado SCD2: `as_of` histórico devuelve la fila vigente en esa fecha.
- Fallback a defaults Python cuando la tabla está vacía (con warning).
- Idempotencia del seed.
- Cierre SCD2 (insertar nueva versión cerrando la previa).
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mlmonitor.data.aggregation_rules import (
    DEFAULT_AGGREGATION_RULES,
    load_aggregation_rules,
    seed_default_global_rules,
)
from mlmonitor.db.models import Base, MetaAggregationRules, MetaModelRegistry


@pytest.fixture
def empty_session():
    """Engine SQLite limpio (sin seed) por test."""
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)
    sess = Session()
    yield sess
    sess.close()


@pytest.fixture
def seeded_session(empty_session):
    """Empty session + seed global con valid_from=2025-01-01."""
    seed_default_global_rules(empty_session, valid_from=date(2025, 1, 1))
    return empty_session


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


def test_seed_inserts_three_global_rules(empty_session):
    inserted = seed_default_global_rules(empty_session, valid_from=date(2025, 1, 1))
    assert inserted == 3

    rows = empty_session.query(MetaAggregationRules).all()
    assert len(rows) == 3
    names = {r.rule_name for r in rows}
    assert names == set(DEFAULT_AGGREGATION_RULES.keys())
    for r in rows:
        assert r.model_registry_id is None
        assert r.valid_to is None
        assert r.rule_value == DEFAULT_AGGREGATION_RULES[r.rule_name]


def test_seed_is_idempotent(seeded_session):
    # Re-correr el seed no duplica filas.
    inserted = seed_default_global_rules(seeded_session, valid_from=date(2025, 6, 1))
    assert inserted == 0
    assert seeded_session.query(MetaAggregationRules).count() == 3


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def test_load_uses_global_when_no_override(seeded_session):
    rules = load_aggregation_rules(seeded_session)
    assert rules == {k: float(v) for k, v in DEFAULT_AGGREGATION_RULES.items()}


def test_load_fallback_to_python_defaults_when_db_empty(empty_session, caplog):
    rules = load_aggregation_rules(empty_session)
    assert rules == {k: float(v) for k, v in DEFAULT_AGGREGATION_RULES.items()}
    # Debe haber logueado warning por cada regla faltante.
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("META_AGGREGATION_RULES" in r.message for r in warnings)


def test_load_model_specific_overrides_global(seeded_session):
    # Insertar un modelo en META_MODEL_REGISTRY para asociar el override.
    reg = MetaModelRegistry(
        model_id="TEST", submodel_id="s1", model_name="t", model_type="scorecard",
        valid_from=date(2025, 1, 1), valid_to=None,
    )
    seeded_session.add(reg)
    seeded_session.flush()

    override = MetaAggregationRules(
        model_registry_id=reg.id,
        rule_name="status_crit_count_to_critical",
        rule_value=3.0,
        valid_from=date(2025, 1, 1),
        valid_to=None,
    )
    seeded_session.add(override)
    seeded_session.flush()

    rules = load_aggregation_rules(seeded_session, model_registry_id=reg.id)
    # Specific override gana
    assert rules["status_crit_count_to_critical"] == 3.0
    # Globales siguen vigentes para las otras dos
    assert rules["status_crit_count_to_warning"] == DEFAULT_AGGREGATION_RULES["status_crit_count_to_warning"]
    assert rules["status_warn_count_to_warning"] == DEFAULT_AGGREGATION_RULES["status_warn_count_to_warning"]


def test_load_global_unchanged_when_specific_not_supplied(seeded_session):
    # Si el caller no pasa model_registry_id, los overrides de otro modelo no afectan.
    reg = MetaModelRegistry(
        model_id="TEST", submodel_id="s1", model_name="t", model_type="scorecard",
        valid_from=date(2025, 1, 1), valid_to=None,
    )
    seeded_session.add(reg)
    seeded_session.flush()
    seeded_session.add(MetaAggregationRules(
        model_registry_id=reg.id,
        rule_name="status_crit_count_to_critical",
        rule_value=99.0,
        valid_from=date(2025, 1, 1),
        valid_to=None,
    ))
    seeded_session.flush()

    rules = load_aggregation_rules(seeded_session)  # sin model_registry_id
    assert rules["status_crit_count_to_critical"] == DEFAULT_AGGREGATION_RULES["status_crit_count_to_critical"]


# ---------------------------------------------------------------------------
# SCD2 (versionado en el tiempo)
# ---------------------------------------------------------------------------


def test_scd2_as_of_returns_historical_value(empty_session):
    """Una regla cambia en 2026-03-01: as_of=2026-01-15 debe leer el valor viejo."""
    # Versión vieja: válida del 2025-01-01 al 2026-02-28.
    empty_session.add(MetaAggregationRules(
        model_registry_id=None,
        rule_name="status_crit_count_to_critical",
        rule_value=3.0,
        valid_from=date(2025, 1, 1),
        valid_to=date(2026, 2, 28),
    ))
    # Versión nueva: válida desde 2026-03-01 hasta hoy.
    empty_session.add(MetaAggregationRules(
        model_registry_id=None,
        rule_name="status_crit_count_to_critical",
        rule_value=8.0,
        valid_from=date(2026, 3, 1),
        valid_to=None,
    ))
    # Seed mínimo de las otras dos para que el resolver no warne.
    empty_session.add(MetaAggregationRules(
        model_registry_id=None,
        rule_name="status_crit_count_to_warning",
        rule_value=5.0,
        valid_from=date(2025, 1, 1),
        valid_to=None,
    ))
    empty_session.add(MetaAggregationRules(
        model_registry_id=None,
        rule_name="status_warn_count_to_warning",
        rule_value=8.0,
        valid_from=date(2025, 1, 1),
        valid_to=None,
    ))
    empty_session.flush()

    rules_old = load_aggregation_rules(empty_session, as_of=date(2026, 1, 15))
    assert rules_old["status_crit_count_to_critical"] == 3.0

    rules_now = load_aggregation_rules(empty_session)  # as_of=None → fila con valid_to IS NULL
    assert rules_now["status_crit_count_to_critical"] == 8.0


def test_scd2_close_previous_then_insert_new(seeded_session):
    """Patrón canónico: cerrar valid_to de la fila vigente, insertar nueva versión.

    Tras el cambio, `load` con as_of futuro lee la nueva; as_of anterior lee la vieja.
    """
    # Cerrar fila previa el día antes de la nueva.
    old = (
        seeded_session.query(MetaAggregationRules)
        .filter(
            MetaAggregationRules.rule_name == "status_crit_count_to_critical",
            MetaAggregationRules.model_registry_id.is_(None),
            MetaAggregationRules.valid_to.is_(None),
        )
        .one()
    )
    new_from = date(2026, 5, 11)
    old.valid_to = new_from - timedelta(days=1)
    seeded_session.add(MetaAggregationRules(
        model_registry_id=None,
        rule_name="status_crit_count_to_critical",
        rule_value=12.0,
        valid_from=new_from,
        valid_to=None,
    ))
    seeded_session.flush()

    pre = load_aggregation_rules(seeded_session, as_of=date(2026, 5, 1))
    assert pre["status_crit_count_to_critical"] == 8.0
    post = load_aggregation_rules(seeded_session)
    assert post["status_crit_count_to_critical"] == 12.0
