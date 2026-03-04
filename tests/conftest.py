"""
Fixtures para tests — SQLite in-memory con datos dummy pre-cargados.
"""

import sys
from pathlib import Path

# Agregar src al path de tests
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from mlmonitor.db.models import Base, MetaMetricThresholds, MetaModelRegistry, MetaVariables
from mlmonitor.db.session import get_session
from mlmonitor.data.dummy_generator import DummyDataGenerator, _week_date


@pytest.fixture(scope="session")
def engine():
    """Engine SQLite in-memory compartido para la sesión de tests."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture(scope="session")
def populated_engine(engine):
    """Engine con datos dummy pre-cargados."""
    with get_session(engine) as session:
        generator = DummyDataGenerator(session, seed=42)
        generator.run()
    return engine


@pytest.fixture
def session(populated_engine):
    """Sesión de DB para cada test (no hace commit entre tests)."""
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(bind=populated_engine)
    sess = SessionLocal()
    yield sess
    sess.close()


@pytest.fixture
def model_id():
    return "BAZBOOST_V1"


@pytest.fixture
def current_week():
    """Semana 20 — la más reciente en el dataset."""
    return _week_date(20)


@pytest.fixture
def performance_week():
    """Semana 8 — con lag de 8 semanas desde semana 16."""
    return _week_date(8)


# ---------------------------------------------------------------------------
# Helpers para resolver IDs surrogados del nuevo star schema
# ---------------------------------------------------------------------------

@pytest.fixture
def segment_ids(session, model_id):
    """
    Retorna {fleet_id: model_registry_id} para el modelo dado.
    Ejemplo: {"s1": 1, "s2": 2, ...}
    """
    regs = (
        session.query(MetaModelRegistry)
        .filter(
            MetaModelRegistry.model_id == model_id,
            MetaModelRegistry.valid_to.is_(None),
        )
        .all()
    )
    return {r.fleet_id: r.id for r in regs}


@pytest.fixture
def variable_ids(session, segment_ids):
    """
    Retorna {fleet_id: {var_name: var_id}} para todos los segmentos.
    Ejemplo: {"s1": {"capacidad_pago": 1, ...}, ...}
    """
    result = {}
    for fleet_id, reg_id in segment_ids.items():
        vars_ = (
            session.query(MetaVariables)
            .filter(
                MetaVariables.model_registry_id == reg_id,
                MetaVariables.valid_to.is_(None),
            )
            .all()
        )
        result[fleet_id] = {v.variable_name: v.id for v in vars_}
    return result


@pytest.fixture
def metric_name_map(session):
    """
    Retorna {metric_id: metric_name} desde META_METRIC_THRESHOLDS.
    Útil para resolver nombres al leer FACT_METRICS_HISTORY.
    """
    rows = (
        session.query(MetaMetricThresholds)
        .filter(MetaMetricThresholds.valid_to.is_(None))
        .all()
    )
    return {r.id: r.metric_name for r in rows}


def get_variable_map(session, model_registry_id: int) -> dict[int, str]:
    """Helper funcional: retorna {var_id: var_name} para un model_registry_id."""
    vars_ = (
        session.query(MetaVariables)
        .filter(
            MetaVariables.model_registry_id == model_registry_id,
            MetaVariables.valid_to.is_(None),
        )
        .all()
    )
    return {v.id: v.variable_name for v in vars_}
