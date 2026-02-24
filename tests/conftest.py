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

from mlmonitor.db.models import Base
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
    return "SCORECARD_CREDITO_COBRANZA_V1"


@pytest.fixture
def current_week():
    """Semana 20 — la más reciente en el dataset."""
    return _week_date(20)


@pytest.fixture
def performance_week():
    """Semana 8 — con lag de 8 semanas desde semana 16."""
    return _week_date(8)
