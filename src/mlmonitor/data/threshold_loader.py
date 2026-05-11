"""
Loader de thresholds per-segmento desde CSV.

Fuente: el CSV `thresholds.csv` dentro del directorio del modelo (
`data/inputs/model_configs/<model_id>/thresholds.csv`). Antes vivía en
`data/inputs/raw_tables/tresholds_monitoreo.csv` (con typo); migrado a
nombre canónico al externalizar la config (ver ADR §8.2.30).

Reglas que aplica este módulo:
- `direction` se determina con la regla canónica en código (ignorando el campo
  del CSV, que viene con errores humanos): `psi`/`null_rate`/`ordering_violations`
  son `higher_worse`; `gini`/`ks` son `lower_worse`.
- Variables intermedias (en `config.extra_serc_variables`, fuera del scorecard)
  se ignoran: no se monitorean.
- Si una métrica esperada no está en el CSV → se inserta con default.
- Si el CSV trae una métrica/variable no esperada → se ignora.

Las funciones reciben `config: ModelConfig` para resolver targets, variables
canónicas por segmento, y el mapeo SERC→canónico — antes esto se importaba
como módulo-level desde `bootstrap` y `variable_mapping`. Ver ADR §8.2.30.
"""

from __future__ import annotations

import csv
from pathlib import Path

from mlmonitor.data.model_config import ModelConfig

DEFAULT_PSI: tuple[float, float] = (0.10, 0.20)
DEFAULT_NULL_RATE: tuple[float, float] = (0.03, 0.10)
DEFAULT_GINI_TARGET: tuple[float, float] = (0.35, 0.25)
DEFAULT_KS_TARGET: tuple[float, float] = (0.20, 0.15)
DEFAULT_ORD_TARGET: tuple[float, float] = (1.0, 2.0)
DEFAULT_GINI_VAR: tuple[float, float] = (0.15, 0.05)

CANONICAL_DIRECTION: dict[str, str] = {
    "psi": "higher_worse",
    "null_rate": "higher_worse",
    "ordering_violations": "higher_worse",
    "gini": "lower_worse",
    "ks": "lower_worse",
}

_PERF_PREFIXES = ("ordering_violations_", "gini_", "ks_")
_BASIC_METRICS = {"psi", "null_rate"}


def _direction_for(metric_name: str) -> str:
    if metric_name in CANONICAL_DIRECTION:
        return CANONICAL_DIRECTION[metric_name]
    for prefix, direction in CANONICAL_DIRECTION.items():
        if metric_name.startswith(prefix + "_"):
            return direction
    raise ValueError(f"No canonical direction for metric '{metric_name}'")


def _normalize_metric_name(raw_metric: str, config: ModelConfig) -> str | None:
    """
    Convierte el `metric_name` del CSV a su forma canónica.

    Returns:
        - "psi" o "null_rate" sin cambios.
        - "gini_<canonical>" / "ks_<canonical>" / "ordering_violations_<canonical>"
          si `<subject>` mapea a una variable de scorecard o es un target conocido.
        - None si la fila debe ignorarse (INTERCEPTO, EXTRA_SERC, desconocida).
    """
    name = raw_metric.strip()
    if name in _BASIC_METRICS:
        return name
    extra_set = {v.upper() for v in config.extra_serc_variables}
    target_names = set(config.target_names)
    for prefix in _PERF_PREFIXES:
        if name.startswith(prefix):
            subject = name[len(prefix):]
            if subject in target_names:
                return name
            if subject.upper() == "INTERCEPTO":
                return None
            canonical = config.serc_to_canonical(subject)
            if canonical:
                return f"{prefix}{canonical}"
            if subject.upper() in extra_set:
                return None
            return None
    return None


def parse_thresholds_csv(
    csv_path: Path, config: ModelConfig,
) -> dict[tuple[str, str], tuple[float, float]]:
    """
    Lee el CSV y retorna un lookup `(segment_id, metric_name) -> (warning, critical)`.

    `segment_id` es la convención interna `s1..s11` (el CSV trae `bb_<n>`).
    Filtra filas vacías, INTERCEPTO, variables intermedias y desconocidas.
    Ignora `direction`/`valid_from` del CSV: la dirección se aplica en código.
    """
    lookup: dict[tuple[str, str], tuple[float, float]] = {}
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            metric_raw = (row.get("metric_name") or "").strip()
            seg_raw = (row.get("modelo_registry_id") or "").strip()
            warn_raw = (row.get("warning_treshold") or "").strip()
            crit_raw = (row.get("critical_treshold") or "").strip()
            if not metric_raw or not seg_raw or not warn_raw or not crit_raw:
                continue
            metric_name = _normalize_metric_name(metric_raw, config)
            if metric_name is None:
                continue
            if not seg_raw.startswith("bb_"):
                continue
            segment_id = "s" + seg_raw.removeprefix("bb_")
            try:
                warning = float(warn_raw)
                critical = float(crit_raw)
            except ValueError:
                continue
            lookup[(segment_id, metric_name)] = (warning, critical)
    return lookup


def expected_metrics_for_segment(
    segment_id: str, config: ModelConfig,
) -> list[tuple[str, tuple[float, float]]]:
    """
    Lista las métricas que se esperan persistir para un segmento, con sus defaults.

    Args:
        segment_id: ID del segmento ("s1", "s2", ...).
        config: configuración del modelo.

    Returns: [(metric_name, (default_warning, default_critical)), ...]
    """
    out: list[tuple[str, tuple[float, float]]] = [
        ("psi", DEFAULT_PSI),
        ("null_rate", DEFAULT_NULL_RATE),
    ]
    for target in config.targets:
        out.append((f"gini_{target.name}", DEFAULT_GINI_TARGET))
        out.append((f"ks_{target.name}", DEFAULT_KS_TARGET))
        out.append((f"ordering_violations_{target.name}", DEFAULT_ORD_TARGET))
    seg = config.segment_by_id(segment_id)
    for canonical_var in seg.variables:
        out.append((f"gini_{canonical_var}", DEFAULT_GINI_VAR))
    return out


def compute_thresholds_for_segment(
    segment_id: str,
    registry_id: int,
    csv_lookup: dict[tuple[str, str], tuple[float, float]],
    config: ModelConfig,
) -> list[dict]:
    """
    Construye los kwargs para crear `MetaMetricThresholds` para un segmento.

    Cada dict puede pasarse directo a `MetaMetricThresholds(**kwargs)` (faltan
    `valid_from` y `valid_to`, que los pone el caller).
    """
    rows: list[dict] = []
    for metric_name, default in expected_metrics_for_segment(segment_id, config):
        warning, critical = csv_lookup.get((segment_id, metric_name), default)
        rows.append({
            "metric_name": metric_name,
            "model_registry_id": registry_id,
            "warning_threshold": warning,
            "critical_threshold": critical,
            "direction": _direction_for(metric_name),
        })
    return rows
