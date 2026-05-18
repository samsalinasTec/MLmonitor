"""
PipelineRunRecorder — persiste el histórico operativo de cada `run` del pipeline
en FACT_PIPELINE_RUNS (Iter 3 §E1).

Cada operación que toca la DB abre/cierra su propia mini-sesión vía
`get_session(engine)`, de forma que la fila se persiste aunque los steps
del orchestrator hagan rollback en sus propias sesiones.

Convención de step names (alineada con `results["steps"]` del orchestrator):
    "metrics" | "report" | "pdf" | "s3" | "email"
"""

from datetime import date, datetime, timezone

from sqlalchemy.engine import Engine

from mlmonitor.db.models import FactPipelineRuns
from mlmonitor.db.session import get_session


_STEP_TO_COLUMN = {
    "metrics": "metrics_step_seconds",
    "report": "report_step_seconds",
    "pdf": "pdf_step_seconds",
    "s3": "s3_step_seconds",
    "email": "email_step_seconds",
}


class PipelineRunRecorder:
    """Registra una fila en FACT_PIPELINE_RUNS por invocación del pipeline."""

    def __init__(
        self,
        engine: Engine,
        model_registry_id: int,
        calculation_week: date,
    ):
        self.engine = engine
        self.model_registry_id = model_registry_id
        self.calculation_week = calculation_week
        self.run_id: int | None = None
        self._step_timings: dict[str, float] = {}
        self._s3_uri: str | None = None

    def start(self) -> int:
        """INSERT inicial con status="running". Devuelve el id asignado."""
        with get_session(self.engine) as session:
            row = FactPipelineRuns(
                model_registry_id=self.model_registry_id,
                calculation_week=self.calculation_week,
                started_at=datetime.now(timezone.utc),
                status="running",
            )
            session.add(row)
            session.flush()
            self.run_id = row.id
        return self.run_id

    def record_step(self, name: str, seconds: float) -> None:
        """Cachea el timing de un step. Se persiste al llamar `finish`."""
        if name not in _STEP_TO_COLUMN:
            raise ValueError(
                f"Step name desconocido: {name!r}. Válidos: {sorted(_STEP_TO_COLUMN)}"
            )
        self._step_timings[name] = float(seconds)

    def set_s3_uri(self, uri: str | None) -> None:
        self._s3_uri = uri

    def finish(
        self,
        status: str,
        fleet_summary: dict | None = None,
        error_message: str | None = None,
        error_stack: str | None = None,
    ) -> None:
        """UPDATE final: timings, status, finished_at, fleet_summary, error_*."""
        if self.run_id is None:
            raise RuntimeError("finish() llamado antes de start()")
        if status not in {"running", "success", "partial", "failed"}:
            raise ValueError(f"status inválido: {status!r}")

        with get_session(self.engine) as session:
            row = session.get(FactPipelineRuns, self.run_id)
            if row is None:
                raise RuntimeError(
                    f"FACT_PIPELINE_RUNS id={self.run_id} desapareció entre start() y finish()"
                )
            row.finished_at = datetime.now(timezone.utc)
            row.status = status
            for step_name, column in _STEP_TO_COLUMN.items():
                if step_name in self._step_timings:
                    setattr(row, column, self._step_timings[step_name])
            row.s3_uri = self._s3_uri
            row.fleet_summary = fleet_summary
            row.error_message = error_message
            row.error_stack = error_stack
