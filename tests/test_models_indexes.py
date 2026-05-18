"""
Tests para los índices declarados en `db/models.py` (Iter 3 §B1+§B2).

Validan que las declaraciones SQLAlchemy de `Index(...)` estén presentes y
con las columnas/dialect-options correctas — no requiere abrir una sesión.
"""

from mlmonitor.db.models import (
    FactPerformanceBinned,
    FactPerformanceIndividual,
    FactPipelineRuns,
    MetaMetricThresholds,
)


def _index_by_name(table, name: str):
    matches = [ix for ix in table.indexes if ix.name == name]
    assert matches, (
        f"índice {name!r} no está declarado en {table.name}. "
        f"Índices presentes: {sorted(ix.name for ix in table.indexes)}"
    )
    return matches[0]


def test_b1_fact_perf_individual_lookup_index_exists():
    """B1 — índice compuesto en FACT_PERFORMANCE_INDIVIDUAL para queries de Gini/KS."""
    ix = _index_by_name(
        FactPerformanceIndividual.__table__, "ix_fact_perf_individual_lookup"
    )
    assert [c.name for c in ix.columns] == [
        "model_registry_id", "origination_week", "ventana",
    ]


def test_b2_fact_perf_binned_metric_index_exists():
    """B2 — índice en FACT_PERFORMANCE_BINNED saltando execution_week."""
    ix = _index_by_name(
        FactPerformanceBinned.__table__, "ix_fact_perf_binned_metric"
    )
    assert [c.name for c in ix.columns] == [
        "model_registry_id", "origination_week", "metric_type",
    ]


def test_b2_meta_metric_thresholds_active_is_partial():
    """B2 — índice parcial sobre filas vigentes (valid_to IS NULL)."""
    ix = _index_by_name(
        MetaMetricThresholds.__table__, "ix_meta_metric_thresholds_active"
    )
    assert [c.name for c in ix.columns] == ["valid_to"]
    # SQLAlchemy expone el predicado del partial via dialect_options.
    pg_opts = ix.dialect_options.get("postgresql", {})
    sqlite_opts = ix.dialect_options.get("sqlite", {})
    assert pg_opts.get("where") is not None, "falta postgresql_where=text('valid_to IS NULL')"
    assert sqlite_opts.get("where") is not None, "falta sqlite_where=text('valid_to IS NULL')"


def test_e1_fact_pipeline_runs_lookup_index_exists():
    """E1 — índice de tendencia en FACT_PIPELINE_RUNS."""
    ix = _index_by_name(
        FactPipelineRuns.__table__, "ix_fact_pipeline_runs_lookup"
    )
    assert [c.name for c in ix.columns] == [
        "model_registry_id", "calculation_week", "started_at",
    ]
