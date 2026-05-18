"""
Helpers para resolver qué modelos procesar en runtime.

Convención de la herramienta (ver upgrades.md A1, plan rosy-tumbling-biscuit):
- Cuando el usuario pasa --model-id <X>, se procesa solo ese modelo.
- Cuando NO lo pasa, se procesan TODOS los modelos activos
  (META_MODEL_REGISTRY con valid_to IS NULL).
- Si no hay modelos activos, se levanta ValueError con mensaje claro.

Esto soporta el caso multi-modelo: el cron semanal de ECS corre los scripts
sin --model-id y procesa toda la flota multi-modelo en una invocación.
"""

from sqlalchemy import distinct
from sqlalchemy.orm import Session

from mlmonitor.db.models import MetaModelRegistry


def resolve_model_ids(session: Session, explicit: str | None) -> list[str]:
    """
    Resuelve la lista de model_id a procesar.

    Args:
        session: sesión activa de SQLAlchemy.
        explicit: model_id explícito (de --model-id). Si None, se auto-detectan
            todos los modelos activos.

    Returns:
        Lista de model_id (strings). Siempre con al menos un elemento.

    Raises:
        ValueError: si explicit es None y no hay modelos activos en
            META_MODEL_REGISTRY.
    """
    if explicit:
        return [explicit]

    rows = (
        session.query(distinct(MetaModelRegistry.model_id))
        .filter(MetaModelRegistry.valid_to.is_(None))
        .all()
    )
    model_ids = [r[0] for r in rows]
    if not model_ids:
        raise ValueError(
            "No hay modelos activos en META_MODEL_REGISTRY. "
            "¿Olvidaste correr el bootstrap? "
            "Ejecuta: poetry run python scripts/run_bootstrap.py --model-id <ID>"
        )
    return sorted(model_ids)


def resolve_model_registry_id(session: Session, model_id: str) -> int:
    """
    Resuelve el `id` de la primera fila activa de `META_MODEL_REGISTRY` para un
    `model_id`. Útil para FACTs que necesitan FK al registry (ej. FACT_PIPELINE_RUNS)
    pero no representan a un (sub)segmento específico.

    Como META_MODEL_REGISTRY es SCD2 con N filas por modelo (una por submodel_id /
    segmento), se toma el menor `submodel_id` activo — mismo criterio que
    `ReportBuilder` para resolver `primary_target_variable`.

    Raises:
        ValueError: si no hay ningún registro activo para `model_id`.
    """
    row = (
        session.query(MetaModelRegistry)
        .filter(
            MetaModelRegistry.model_id == model_id,
            MetaModelRegistry.valid_to.is_(None),
        )
        .order_by(MetaModelRegistry.submodel_id)
        .first()
    )
    if row is None:
        raise ValueError(
            f"No hay registro activo en META_MODEL_REGISTRY para model_id={model_id!r}. "
            "¿Falta correr el bootstrap?"
        )
    return row.id
