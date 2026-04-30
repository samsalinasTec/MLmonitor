"""Tests del módulo `data/threshold_loader.py`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from mlmonitor.data.bootstrap import TARGET_VARIABLES
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
from mlmonitor.data.variable_mapping import CANONICAL_VARIABLES


CSV_HEADER = "id,metric_name,modelo_registry_id,warning_treshold,critical_treshold,direction,valid_from,valid_to\n"


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


def test_normalize_metric_name():
    assert _normalize_metric_name("psi") == "psi"
    assert _normalize_metric_name("null_rate") == "null_rate"
    assert _normalize_metric_name("gini_b_malo4_6") == "gini_b_malo4_6"
    # SERC → canónico
    assert _normalize_metric_name("gini_EDAD") == "gini_edad"
    assert _normalize_metric_name("gini_SEXO") == "gini_fisexo"
    # Casos a ignorar
    assert _normalize_metric_name("gini_INTERCEPTO") is None
    assert _normalize_metric_name("gini_CPTI813REZ") is None  # EXTRA_SERC
    assert _normalize_metric_name("gini_VARIABLE_NO_EXISTE") is None


def test_parse_thresholds_csv_filters_and_maps(tmp_path: Path):
    rows = [
        "1,psi,bb_1,0.2,0.25,higher_worse,01/01/25,",
        "2,null_rate,bb_1,0.03,0.1,higher_worse,01/01/25,",
        "3,gini_b_malo4_6,bb_1,0.3,0.2,lower_worse,01/01/25,",
        "4,gini_EDAD,bb_1,0.15,0.05,lower_worse,01/01/25,",
        "5,gini_INTERCEPTO,bb_1,0.5,0.4,lower_worse,01/01/25,",     # ignorar
        "6,gini_CPTI813REZ,bb_1,0.5,0.4,lower_worse,01/01/25,",     # EXTRA_SERC
        "7,gini_NOEXISTE,bb_1,0.5,0.4,lower_worse,01/01/25,",       # unknown
        "8,psi,,0.2,0.25,higher_worse,01/01/25,",                   # sin segmento
        ",,,,,,,",                                                  # vacía
    ]
    csv = _write_csv(tmp_path / "thr.csv", rows)
    lookup = parse_thresholds_csv(csv)
    assert lookup[("s1", "psi")] == (0.2, 0.25)
    assert lookup[("s1", "null_rate")] == (0.03, 0.1)
    assert lookup[("s1", "gini_b_malo4_6")] == (0.3, 0.2)
    assert lookup[("s1", "gini_edad")] == (0.15, 0.05)
    # Filtradas
    assert ("s1", "gini_INTERCEPTO") not in lookup
    assert ("s1", "gini_CPTI813REZ") not in lookup
    assert ("s1", "gini_NOEXISTE") not in lookup
    assert len(lookup) == 4


def test_expected_metrics_for_segment_counts():
    n_targets = len(TARGET_VARIABLES)
    for seg in range(1, 12):
        n_vars = len(CANONICAL_VARIABLES.get(seg, []))
        expected = expected_metrics_for_segment(seg)
        assert len(expected) == 2 + n_targets * 3 + n_vars


def test_compute_thresholds_uses_csv_when_present():
    csv_lookup = {("s1", "psi"): (0.5, 0.7)}
    rows = compute_thresholds_for_segment("s1", registry_id=42, csv_lookup=csv_lookup)
    psi_row = next(r for r in rows if r["metric_name"] == "psi")
    assert psi_row["warning_threshold"] == 0.5
    assert psi_row["critical_threshold"] == 0.7
    assert psi_row["direction"] == "higher_worse"
    assert psi_row["model_registry_id"] == 42


def test_compute_thresholds_uses_defaults_when_missing():
    rows = compute_thresholds_for_segment("s1", registry_id=42, csv_lookup={})
    by_name = {r["metric_name"]: r for r in rows}
    assert (by_name["psi"]["warning_threshold"], by_name["psi"]["critical_threshold"]) == DEFAULT_PSI
    assert (by_name["null_rate"]["warning_threshold"], by_name["null_rate"]["critical_threshold"]) == DEFAULT_NULL_RATE
    assert (by_name["gini_b_malo4_6"]["warning_threshold"], by_name["gini_b_malo4_6"]["critical_threshold"]) == DEFAULT_GINI_TARGET
    assert (by_name["ks_b_malo4_6"]["warning_threshold"], by_name["ks_b_malo4_6"]["critical_threshold"]) == DEFAULT_KS_TARGET
    assert (by_name["ordering_violations_b_malo4_6"]["warning_threshold"], by_name["ordering_violations_b_malo4_6"]["critical_threshold"]) == DEFAULT_ORD_TARGET
    # gini_<scorecard_var> defaults
    first_var = CANONICAL_VARIABLES[1][0]
    var_row = by_name[f"gini_{first_var}"]
    assert (var_row["warning_threshold"], var_row["critical_threshold"]) == DEFAULT_GINI_VAR


def test_compute_thresholds_overrides_direction_in_csv():
    """Aunque el CSV trajera direction invertida, el loader aplica la regla canónica."""
    csv_lookup = {("s1", "gini_b_malo4_6"): (0.3, 0.2)}
    rows = compute_thresholds_for_segment("s1", registry_id=1, csv_lookup=csv_lookup)
    target_row = next(r for r in rows if r["metric_name"] == "gini_b_malo4_6")
    assert target_row["direction"] == "lower_worse"


def test_total_count_across_all_segments():
    """Smoke: con un csv vacío, contar todas las filas que se generarían en bootstrap."""
    total = 0
    for seg in range(1, 12):
        total += len(compute_thresholds_for_segment(f"s{seg}", registry_id=seg, csv_lookup={}))
    expected_total = (
        11 * (2 + len(TARGET_VARIABLES) * 3)  # 11 × (psi + null_rate + n_targets × 3)
        + sum(len(CANONICAL_VARIABLES[s]) for s in range(1, 12))
    )
    assert total == expected_total


def test_parse_thresholds_csv_real_file():
    """Smoke contra el CSV real entregado por crédito."""
    real_csv = Path(__file__).parent.parent / "data" / "inputs" / "raw_tables" / "tresholds_monitoreo.csv"
    if not real_csv.exists():
        pytest.skip("CSV real no disponible en este checkout")
    lookup = parse_thresholds_csv(real_csv)
    # 11 segmentos × (2 básicas + n_targets × 3) filas mínimas que SÍ deben mapear.
    # Más las gini_<scorecard_var> que mapean.
    min_expected = 11 * (2 + len(TARGET_VARIABLES) * 3)
    assert len(lookup) >= min_expected
    # psi y null_rate por segmento están presentes
    for seg in range(1, 12):
        assert (f"s{seg}", "psi") in lookup
        assert (f"s{seg}", "null_rate") in lookup
