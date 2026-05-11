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
