"""
Resolver de reglas de agregación de severidad (`META_AGGREGATION_RULES`).

Iteración 2 A3 sacó las constantes `status_*_count_*` de `config/settings.py`
y las movió a una tabla SCD2 — versionable, auditable, override por modelo.

Precedencia (calcada de `AlertEvaluator.get_threshold` en metrics/calculator.py):
1. Fila específica del modelo (`model_registry_id == arg`) vigente.
2. Fila global (`model_registry_id IS NULL`) vigente.
3. Default Python (`DEFAULT_AGGREGATION_RULES`) — fallback de último recurso
   para que el sistema no explote si la tabla está vacía (ej. tests sin seed).

"Vigente" = `valid_to IS NULL` o `valid_to >= as_of`, combinado con
`valid_from <= as_of`. Si no se pasa `as_of` se usa la fila con `valid_to IS NULL`
(comportamiento "ahora").

Las reglas se siembran en `bootstrap.py::ModelBootstrap._populate_meta_aggregation_rules`
como globales con los valores actuales.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import or_
from sqlalchemy.orm import Session

from mlmonitor.db.models import MetaAggregationRules

logger = logging.getLogger(__name__)


# Defaults Python — última red de seguridad. Coinciden con los valores
# usados en `config/settings.py` antes de A3 (post-ajuste 2026-05-05).
DEFAULT_AGGREGATION_RULES: dict[str, float] = {
    "status_crit_count_to_critical": 8.0,
    "status_crit_count_to_warning": 5.0,
    "status_warn_count_to_warning": 8.0,
}


# Descripciones legibles, persistidas en la columna `description` del seed.
RULE_DESCRIPTIONS: dict[str, str] = {
    "status_crit_count_to_critical": (
        "Alertas críticas agregables ≥ N → CRITICAL"
    ),
    "status_crit_count_to_warning": (
        "Alertas críticas agregables ≥ N → WARNING (cuando no llega a CRITICAL)"
    ),
    "status_warn_count_to_warning": (
        "Alertas WARNING agregables ≥ N → WARNING"
    ),
}


def load_aggregation_rules(
    session: Session,
    model_registry_id: int | None = None,
    as_of: date | None = None,
) -> dict[str, float]:
    """Devuelve un dict {rule_name: rule_value} resuelto por precedencia.

    Para Iteración 2 el caller pasa `model_registry_id=None` (las reglas
    son fleet-level; cada SegmentMetrics se evalúa con el mismo dict). La
    firma soporta el caso futuro de override por modelo.

    Si la tabla está vacía o le falta una regla, se cae a `DEFAULT_AGGREGATION_RULES`
    con `logger.warning`. Esto preserva el comportamiento histórico pre-A3
    para escenarios degradados.
    """
    rows = _query_active_rules(session, as_of)

    by_scope: dict[tuple[str, int | None], MetaAggregationRules] = {}
    for r in rows:
        by_scope[(r.rule_name, r.model_registry_id)] = r

    resolved: dict[str, float] = {}
    for rule_name, default_value in DEFAULT_AGGREGATION_RULES.items():
        specific = by_scope.get((rule_name, model_registry_id)) if model_registry_id is not None else None
        global_row = by_scope.get((rule_name, None))

        if specific is not None and specific.rule_value is not None:
            resolved[rule_name] = float(specific.rule_value)
        elif global_row is not None and global_row.rule_value is not None:
            resolved[rule_name] = float(global_row.rule_value)
        else:
            logger.warning(
                "META_AGGREGATION_RULES sin valor activo para '%s' (model=%s, as_of=%s); "
                "usando default Python %s",
                rule_name, model_registry_id, as_of, default_value,
            )
            resolved[rule_name] = float(default_value)

    return resolved


def _query_active_rules(
    session: Session,
    as_of: date | None,
) -> list[MetaAggregationRules]:
    """Filtra filas vigentes en `as_of` (o "ahora" si as_of es None)."""
    query = session.query(MetaAggregationRules)
    if as_of is None:
        query = query.filter(MetaAggregationRules.valid_to.is_(None))
    else:
        query = query.filter(
            MetaAggregationRules.valid_from <= as_of,
            or_(
                MetaAggregationRules.valid_to.is_(None),
                MetaAggregationRules.valid_to >= as_of,
            ),
        )
    return query.all()


def seed_default_global_rules(
    session: Session,
    valid_from: date,
) -> int:
    """Siembra las 3 reglas globales con los defaults Python.

    Idempotente: si ya existe una fila global vigente para una regla, no la
    duplica. Pensado para invocarse desde `ModelBootstrap._populate_meta_aggregation_rules`
    una sola vez por DB fresca.

    Returns: número de filas insertadas (0 si todas ya existían).
    """
    active_globals = {
        r.rule_name
        for r in session.query(MetaAggregationRules)
        .filter(
            MetaAggregationRules.model_registry_id.is_(None),
            MetaAggregationRules.valid_to.is_(None),
        )
        .all()
    }

    inserted = 0
    for rule_name, value in DEFAULT_AGGREGATION_RULES.items():
        if rule_name in active_globals:
            continue
        session.add(MetaAggregationRules(
            model_registry_id=None,
            rule_name=rule_name,
            rule_value=float(value),
            description=RULE_DESCRIPTIONS.get(rule_name),
            valid_from=valid_from,
            valid_to=None,
        ))
        inserted += 1

    if inserted:
        session.flush()
    return inserted
