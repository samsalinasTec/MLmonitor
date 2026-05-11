"""Tests del módulo `data/threshold_loader.py`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from mlmonitor.data.model_config import ModelConfig
from mlmonitor.data.threshold_loader import (
    DEFAULT_GINI_TARGET,
    DEFAULT_GINI_VAR,
    DEFAULT_KS_TARGET,
    DEFAULT_NULL_RATE,
    DEFAULT_ORD_TARGET,
    DEFAULT_PSI,
    _direction_for,
    _normalize_metric_name,
    compute_thresholds_for_segment,
    expected_metrics_for_segment,
    parse_thresholds_csv,
)


CSV_HEADER = "id,metric_name,modelo_registry_id,warning_treshold,critical_treshold,direction,valid_from,valid_to\n"


@pytest.fixture(scope="module")
def config() -> ModelConfig:
    """Carga la config real de BAZBOOST_V1 una vez por módulo."""
    return ModelConfig.for_model("BAZBOOST_V1")


def _write_csv(path: Path, rows: list[str]) -> Path:
    path.write_text(CSV_HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_direction_canonical():
    assert _direction_for("psi") == "higher_worse"
    assert _direction_for("null_rate") == "higher_worse"
    assert _direction_for("gini_b_malo4_6") == "lower_worse"
    assert _direction_for("ks_b_malo8_13") == "lower_worse"
    assert _direction_for("ordering_violations_b_malo14_26") == "higher_worse"
    assert _direction_for("gini_edad") == "lower_worse"


def test_direction_unknown_raises():
    with pytest.raises(ValueError):
        _direction_for("misterio")


def test_normalize_metric_name(config: ModelConfig):
    assert _normalize_metric_name("psi", config) == "psi"
    assert _normalize_metric_name("null_rate", config) == "null_rate"
    assert _normalize_metric_name("gini_b_malo4_6", config) == "gini_b_malo4_6"
    # SERC → canónico
    assert _normalize_metric_name("gini_EDAD", config) == "gini_edad"
    assert _normalize_metric_name("gini_SEXO", config) == "gini_fisexo"
    # Casos a ignorar
    assert _normalize_metric_name("gini_INTERCEPTO", config) is None
    assert _normalize_metric_name("gini_CPTI813REZ", config) is None  # extra_serc_variables
    assert _normalize_metric_name("gini_VARIABLE_NO_EXISTE", config) is None


def test_parse_thresholds_csv_filters_and_maps(tmp_path: Path, config: ModelConfig):
    rows = [
        "1,psi,bb_1,0.2,0.25,higher_worse,01/01/25,",
        "2,null_rate,bb_1,0.03,0.1,higher_worse,01/01/25,",
        "3,gini_b_malo4_6,bb_1,0.3,0.2,lower_worse,01/01/25,",
        "4,gini_EDAD,bb_1,0.15,0.05,lower_worse,01/01/25,",
        "5,gini_INTERCEPTO,bb_1,0.5,0.4,lower_worse,01/01/25,",     # ignorar
        "6,gini_CPTI813REZ,bb_1,0.5,0.4,lower_worse,01/01/25,",     # extra_serc
        "7,gini_NOEXISTE,bb_1,0.5,0.4,lower_worse,01/01/25,",       # unknown
        "8,psi,,0.2,0.25,higher_worse,01/01/25,",                   # sin segmento
        ",,,,,,,",                                                  # vacía
    ]
    csv = _write_csv(tmp_path / "thr.csv", rows)
    lookup = parse_thresholds_csv(csv, config)
    assert lookup[("s1", "psi")] == (0.2, 0.25)
    assert lookup[("s1", "null_rate")] == (0.03, 0.1)
    assert lookup[("s1", "gini_b_malo4_6")] == (0.3, 0.2)
    assert lookup[("s1", "gini_edad")] == (0.15, 0.05)
    # Filtradas
    assert ("s1", "gini_INTERCEPTO") not in lookup
    assert ("s1", "gini_CPTI813REZ") not in lookup
    assert ("s1", "gini_NOEXISTE") not in lookup
    assert len(lookup) == 4


def test_expected_metrics_for_segment_counts(config: ModelConfig):
    n_targets = len(config.targets)
    for seg in config.segments:
        n_vars = len(seg.variables)
        expected = expected_metrics_for_segment(seg.segment_id, config)
        assert len(expected) == 2 + n_targets * 3 + n_vars


def test_compute_thresholds_uses_csv_when_present(config: ModelConfig):
    csv_lookup = {("s1", "psi"): (0.5, 0.7)}
    rows = compute_thresholds_for_segment(
        "s1", registry_id=42, csv_lookup=csv_lookup, config=config,
    )
    psi_row = next(r for r in rows if r["metric_name"] == "psi")
    assert psi_row["warning_threshold"] == 0.5
    assert psi_row["critical_threshold"] == 0.7
    assert psi_row["direction"] == "higher_worse"
    assert psi_row["model_registry_id"] == 42


def test_compute_thresholds_uses_defaults_when_missing(config: ModelConfig):
    rows = compute_thresholds_for_segment(
        "s1", registry_id=42, csv_lookup={}, config=config,
    )
    by_name = {r["metric_name"]: r for r in rows}
    assert (by_name["psi"]["warning_threshold"], by_name["psi"]["critical_threshold"]) == DEFAULT_PSI
    assert (by_name["null_rate"]["warning_threshold"], by_name["null_rate"]["critical_threshold"]) == DEFAULT_NULL_RATE
    assert (by_name["gini_b_malo4_6"]["warning_threshold"], by_name["gini_b_malo4_6"]["critical_threshold"]) == DEFAULT_GINI_TARGET
    assert (by_name["ks_b_malo4_6"]["warning_threshold"], by_name["ks_b_malo4_6"]["critical_threshold"]) == DEFAULT_KS_TARGET
    assert (by_name["ordering_violations_b_malo4_6"]["warning_threshold"], by_name["ordering_violations_b_malo4_6"]["critical_threshold"]) == DEFAULT_ORD_TARGET
    # gini_<scorecard_var> defaults
    first_var = config.segment_by_id("s1").variables[0]
    var_row = by_name[f"gini_{first_var}"]
    assert (var_row["warning_threshold"], var_row["critical_threshold"]) == DEFAULT_GINI_VAR


def test_compute_thresholds_overrides_direction_in_csv(config: ModelConfig):
    """Aunque el CSV trajera direction invertida, el loader aplica la regla canónica."""
    csv_lookup = {("s1", "gini_b_malo4_6"): (0.3, 0.2)}
    rows = compute_thresholds_for_segment(
        "s1", registry_id=1, csv_lookup=csv_lookup, config=config,
    )
    target_row = next(r for r in rows if r["metric_name"] == "gini_b_malo4_6")
    assert target_row["direction"] == "lower_worse"


def test_total_count_across_all_segments(config: ModelConfig):
    """Smoke: con un csv vacío, contar todas las filas que se generarían en bootstrap."""
    total = 0
    for seg in config.segments:
        total += len(compute_thresholds_for_segment(
            seg.segment_id, registry_id=config.segment_id_int(seg.segment_id),
            csv_lookup={}, config=config,
        ))
    n_segments = len(config.segments)
    n_targets = len(config.targets)
    expected_total = (
        n_segments * (2 + n_targets * 3)  # 11 × (psi + null_rate + n_targets × 3)
        + sum(len(seg.variables) for seg in config.segments)
    )
    assert total == expected_total


def test_parse_thresholds_csv_real_file(config: ModelConfig):
    """Smoke contra el CSV real entregado por crédito (movido al config dir)."""
    real_csv = config.thresholds_csv
    if not real_csv.exists():
        pytest.skip("CSV real no disponible en este checkout")
    lookup = parse_thresholds_csv(real_csv, config)
    # 11 segmentos × (2 básicas + n_targets × 3) filas mínimas que SÍ deben mapear.
    n_targets = len(config.targets)
    n_segments = len(config.segments)
    min_expected = n_segments * (2 + n_targets * 3)
    assert len(lookup) >= min_expected
    # psi y null_rate por segmento están presentes
    for seg in config.segments:
        assert (seg.segment_id, "psi") in lookup
        assert (seg.segment_id, "null_rate") in lookup
