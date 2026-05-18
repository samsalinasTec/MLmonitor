"""
Tests para PipelineRunRecorder (Iter 3 §E1).

Usa un engine SQLite in-memory propio (function-scoped) para que cada test
arranque con FACT_PIPELINE_RUNS vacía y se pueda verificar la convención
append-only sin interferencia entre casos.
"""

from datetime import date

import pytest
from sqlalchemy import create_engine

from mlmonitor.db.models import Base, FactPipelineRuns, MetaModelRegistry
from mlmonitor.db.session import get_session
from mlmonitor.pipeline.run_recorder import PipelineRunRecorder


CALC_WEEK = date(2026, 4, 6)


@pytest.fixture
def engine_with_registry():
    """Engine SQLite in-memory con una única fila de META_MODEL_REGISTRY activa."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    with get_session(eng) as session:
        session.add(MetaModelRegistry(
            model_id="TEST_MODEL_V1",
            submodel_id="s1",
            model_name="Test",
            model_type="scorecard",
            score_min=0, score_max=1000,
            primary_target_variable="test_target",
            valid_from=date(2025, 1, 1),
            valid_to=None,
        ))
        session.flush()
    return eng


def _registry_id(engine):
    with get_session(engine) as session:
        row = session.query(MetaModelRegistry).first()
        return row.id


_FIELDS = (
    "id", "model_registry_id", "calculation_week", "started_at", "finished_at",
    "status", "metrics_step_seconds", "report_step_seconds", "pdf_step_seconds",
    "s3_step_seconds", "email_step_seconds", "s3_uri", "fleet_summary",
    "error_message", "error_stack",
)


def _all_runs(engine):
    """Devuelve la tabla como lista de dicts (evita DetachedInstanceError)."""
    with get_session(engine) as session:
        rows = session.query(FactPipelineRuns).order_by(FactPipelineRuns.id).all()
        return [{f: getattr(r, f) for f in _FIELDS} for r in rows]


def test_start_inserts_running_row(engine_with_registry):
    reg_id = _registry_id(engine_with_registry)
    recorder = PipelineRunRecorder(engine_with_registry, reg_id, CALC_WEEK)
    run_id = recorder.start()

    assert run_id is not None
    runs = _all_runs(engine_with_registry)
    assert len(runs) == 1
    assert runs[0]["status"] == "running"
    assert runs[0]["started_at"] is not None
    assert runs[0]["finished_at"] is None
    assert runs[0]["metrics_step_seconds"] is None  # aún no se llamó record_step


def test_finish_success_persists_all_fields(engine_with_registry):
    reg_id = _registry_id(engine_with_registry)
    recorder = PipelineRunRecorder(engine_with_registry, reg_id, CALC_WEEK)
    recorder.start()
    recorder.record_step("metrics", 1.2)
    recorder.record_step("report", 3.4)
    recorder.record_step("pdf", 0.5)
    recorder.record_step("s3", 0.7)
    recorder.record_step("email", 0.3)
    recorder.set_s3_uri("s3://bucket/reports/r.pdf")
    fleet = {"total": 11, "ok": 6, "warning": 4, "critical": 1}
    recorder.finish("success", fleet_summary=fleet)

    runs = _all_runs(engine_with_registry)
    assert len(runs) == 1
    row = runs[0]
    assert row["status"] == "success"
    assert row["finished_at"] is not None
    assert row["metrics_step_seconds"] == pytest.approx(1.2)
    assert row["report_step_seconds"] == pytest.approx(3.4)
    assert row["pdf_step_seconds"] == pytest.approx(0.5)
    assert row["s3_step_seconds"] == pytest.approx(0.7)
    assert row["email_step_seconds"] == pytest.approx(0.3)
    assert row["s3_uri"] == "s3://bucket/reports/r.pdf"
    assert row["fleet_summary"] == fleet
    assert row["error_message"] is None
    assert row["error_stack"] is None


def test_finish_failed_persists_error(engine_with_registry):
    reg_id = _registry_id(engine_with_registry)
    recorder = PipelineRunRecorder(engine_with_registry, reg_id, CALC_WEEK)
    recorder.start()
    recorder.record_step("metrics", 0.9)
    recorder.finish(
        "failed",
        fleet_summary=None,
        error_message="RuntimeError: boom",
        error_stack="Traceback (most recent call last):\n  ...\nRuntimeError: boom\n",
    )

    [row] = _all_runs(engine_with_registry)
    assert row["status"] == "failed"
    assert row["metrics_step_seconds"] == pytest.approx(0.9)
    assert row["report_step_seconds"] is None  # nunca se ejecutó
    assert row["error_message"] == "RuntimeError: boom"
    assert row["error_stack"].startswith("Traceback")


def test_append_only_two_runs(engine_with_registry):
    """Re-run en la misma (model_registry_id, calculation_week) → 2 filas."""
    reg_id = _registry_id(engine_with_registry)
    for status in ("success", "success"):
        r = PipelineRunRecorder(engine_with_registry, reg_id, CALC_WEEK)
        r.start()
        r.record_step("metrics", 1.0)
        r.finish(status, fleet_summary={"total": 1, "ok": 1, "warning": 0, "critical": 0})

    runs = _all_runs(engine_with_registry)
    assert len(runs) == 2
    assert all(r["calculation_week"] == CALC_WEEK for r in runs)
    assert all(r["model_registry_id"] == reg_id for r in runs)
    assert runs[0]["id"] != runs[1]["id"]


def test_finish_before_start_raises(engine_with_registry):
    reg_id = _registry_id(engine_with_registry)
    recorder = PipelineRunRecorder(engine_with_registry, reg_id, CALC_WEEK)
    with pytest.raises(RuntimeError, match="antes de start"):
        recorder.finish("success")


def test_record_step_unknown_name_raises(engine_with_registry):
    reg_id = _registry_id(engine_with_registry)
    recorder = PipelineRunRecorder(engine_with_registry, reg_id, CALC_WEEK)
    with pytest.raises(ValueError, match="Step name desconocido"):
        recorder.record_step("nonexistent", 1.0)
