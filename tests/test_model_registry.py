"""
Tests para el helper resolve_model_ids.

Cubre los 5 casos del plan:
1. DB vacía + explicit=None → ValueError
2. Un solo modelo activo + explicit=None → lista de uno
3. Dos modelos activos + explicit=None → lista de dos
4. explicit="X" → ["X"] aunque haya muchos activos
5. Filtra correctamente valid_to IS NULL (registros cerrados no aparecen)
"""

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mlmonitor.data.model_registry import resolve_model_ids
from mlmonitor.db.models import Base, MetaModelRegistry


@pytest.fixture
def empty_session():
    """Sesión SQLite in-memory con schema creado pero sin datos."""
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    SessionLocal = sessionmaker(bind=eng)
    sess = SessionLocal()
    yield sess
    sess.close()


def _insert_model(session, model_id: str, valid_to: date | None = None):
    """Helper: inserta un MetaModelRegistry con un solo segmento."""
    session.add(MetaModelRegistry(
        model_id=model_id,
        submodel_id="default",
        model_name=f"Test {model_id}",
        model_type="scorecard",
        score_min=0,
        score_max=1000,
        primary_target_variable="some_target",
        valid_from=date(2024, 1, 1),
        valid_to=valid_to,
    ))
    session.flush()


def test_empty_registry_raises_value_error(empty_session):
    """Con la tabla vacía y sin explicit, debe levantar ValueError."""
    with pytest.raises(ValueError, match="No hay modelos activos"):
        resolve_model_ids(empty_session, None)


def test_single_active_model_returns_singleton_list(empty_session):
    """Con un solo modelo activo, retorna [model_id]."""
    _insert_model(empty_session, "BAZBOOST_V1")
    result = resolve_model_ids(empty_session, None)
    assert result == ["BAZBOOST_V1"]


def test_two_active_models_returns_both(empty_session):
    """Con dos modelos activos y explicit=None, retorna ambos (sorted)."""
    _insert_model(empty_session, "BAZBOOST_V1")
    _insert_model(empty_session, "RIESGO_OP_V1")
    result = resolve_model_ids(empty_session, None)
    # Sorted asegura orden determinístico
    assert result == ["BAZBOOST_V1", "RIESGO_OP_V1"]


def test_explicit_overrides_auto_detection(empty_session):
    """Con explicit='X', retorna ['X'] aunque haya otros modelos activos."""
    _insert_model(empty_session, "BAZBOOST_V1")
    _insert_model(empty_session, "RIESGO_OP_V1")
    result = resolve_model_ids(empty_session, "BAZBOOST_V1")
    assert result == ["BAZBOOST_V1"]


def test_explicit_returns_even_when_not_in_db(empty_session):
    """explicit='X' siempre retorna ['X'] sin validar contra DB.

    Este test documenta el comportamiento intencional: el helper no valida
    si el modelo existe en META_MODEL_REGISTRY. Esa validación queda al
    callsite (orchestrator/ETL fallarán naturalmente al consultar el modelo).
    """
    # DB vacía, pero el llamante pasó un model_id explícito
    result = resolve_model_ids(empty_session, "MODELO_INEXISTENTE")
    assert result == ["MODELO_INEXISTENTE"]


def test_closed_registries_are_excluded(empty_session):
    """Registros con valid_to poblado (cerrados) no deben aparecer."""
    _insert_model(empty_session, "BAZBOOST_V1", valid_to=None)        # vigente
    _insert_model(empty_session, "MODELO_VIEJO", valid_to=date(2025, 1, 1))  # cerrado

    result = resolve_model_ids(empty_session, None)
    assert result == ["BAZBOOST_V1"]
    assert "MODELO_VIEJO" not in result


def test_multiple_segments_same_model_dedup(empty_session):
    """Si un modelo tiene N filas (N segmentos), aparece UNA sola vez en el resultado."""
    # BAZBOOST_V1 con 3 segmentos
    for sub in ["s1", "s2", "s3"]:
        empty_session.add(MetaModelRegistry(
            model_id="BAZBOOST_V1",
            submodel_id=sub,
            model_name="Test",
            model_type="scorecard",
            score_min=0, score_max=1000,
            primary_target_variable="some_target",
            valid_from=date(2024, 1, 1),
            valid_to=None,
        ))
    empty_session.flush()

    result = resolve_model_ids(empty_session, None)
    assert result == ["BAZBOOST_V1"]
    assert len(result) == 1  # dedup por DISTINCT
