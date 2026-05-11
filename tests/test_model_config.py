"""
Tests del módulo `data/model_config.py`.

Cubre carga, validación, resolución de path por convención, y los métodos
helper (serc_to_canonical, is_categorical, segment_by_id, etc.).
"""

import json
from pathlib import Path

import pytest

from mlmonitor.data.model_config import ModelConfig, SegmentConfig, TargetConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_config_dict() -> dict:
    """Dict mínimo válido para construir un ModelConfig en memoria."""
    return {
        "model_id": "TEST_MODEL_V1",
        "model_name": "Test Model",
        "model_type": "scorecard",
        "owner_team": "qa",
        "target_definition": "Probabilidad de evento test",
        "score_min": 0,
        "score_max": 1000,
        "primary_target": "test_target",
        "missing_sentinel": -100,
        "num_bins_numeric": 10,
        "score_bins": [[0, 500], [500, 1000]],
        "categorical_variables": ["sexo_cat"],
        "targets": [
            {"name": "test_target", "lag_semanas": 4, "ascending_order": False}
        ],
        "segments": [
            {
                "segment_id": "s1",
                "group_name": "TEST",
                "feature_count": 2,
                "variables": ["edad", "sexo_cat"],
            },
            {
                "segment_id": "s2",
                "group_name": "TEST",
                "feature_count": 2,
                "variables": ["edad", "ingresos"],
            },
        ],
        "name_mapping": {"SEXO": "sexo_cat", "INGRESOSESPECIAL": "ingresos"},
        "extra_serc_variables": ["VAREXTRA1", "VAREXTRA2"],
    }


@pytest.fixture
def minimal_config_path(tmp_path: Path, minimal_config_dict: dict) -> Path:
    """Crea un config.json mínimo en tmp_path y retorna el path."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(minimal_config_dict), encoding="utf-8")
    return cfg_path


# ---------------------------------------------------------------------------
# Tests de carga
# ---------------------------------------------------------------------------


def test_from_json_file_loads_valid_config(minimal_config_path: Path):
    config = ModelConfig.from_json_file(minimal_config_path)
    assert config.model_id == "TEST_MODEL_V1"
    assert config.primary_target == "test_target"
    assert len(config.segments) == 2
    assert len(config.targets) == 1
    assert isinstance(config.segments[0], SegmentConfig)
    assert isinstance(config.targets[0], TargetConfig)
    assert config.config_dir == minimal_config_path.parent


def test_from_json_file_raises_on_missing_field(tmp_path: Path, minimal_config_dict: dict):
    """Falta campo requerido → ValueError con mensaje claro."""
    del minimal_config_dict["primary_target"]
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(minimal_config_dict), encoding="utf-8")
    with pytest.raises(ValueError, match="campos requeridos"):
        ModelConfig.from_json_file(cfg_path)


def test_from_json_file_raises_when_primary_target_not_in_targets(
    tmp_path: Path, minimal_config_dict: dict
):
    """primary_target debe existir en targets."""
    minimal_config_dict["primary_target"] = "no_existe"
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(minimal_config_dict), encoding="utf-8")
    with pytest.raises(ValueError, match="primary_target"):
        ModelConfig.from_json_file(cfg_path)


def test_from_json_file_raises_when_categorical_not_in_segments(
    tmp_path: Path, minimal_config_dict: dict
):
    """categorical_variables debe referenciar variables presentes en algún segmento."""
    minimal_config_dict["categorical_variables"] = ["variable_fantasma"]
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(minimal_config_dict), encoding="utf-8")
    with pytest.raises(ValueError, match="categorical_variables"):
        ModelConfig.from_json_file(cfg_path)


def test_from_json_file_raises_on_invalid_score_bin(
    tmp_path: Path, minimal_config_dict: dict
):
    """score_bin con hi <= lo → ValueError."""
    minimal_config_dict["score_bins"] = [[100, 50]]
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(minimal_config_dict), encoding="utf-8")
    with pytest.raises(ValueError, match="score_bin"):
        ModelConfig.from_json_file(cfg_path)


def test_from_json_file_raises_on_empty_targets(
    tmp_path: Path, minimal_config_dict: dict
):
    minimal_config_dict["targets"] = []
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(minimal_config_dict), encoding="utf-8")
    with pytest.raises(ValueError, match="targets"):
        ModelConfig.from_json_file(cfg_path)


def test_from_json_file_raises_on_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        ModelConfig.from_json_file(tmp_path / "no_existe.json")


# ---------------------------------------------------------------------------
# Tests de for_model (auto-resolve por convención)
# ---------------------------------------------------------------------------


def test_for_model_auto_resolves_path(tmp_path: Path, minimal_config_dict: dict):
    """for_model resuelve <base_dir>/<model_id_lowercase>/config.json."""
    model_dir = tmp_path / "test_model_v1"
    model_dir.mkdir()
    (model_dir / "config.json").write_text(
        json.dumps(minimal_config_dict), encoding="utf-8"
    )
    config = ModelConfig.for_model("TEST_MODEL_V1", base_dir=tmp_path)
    assert config.model_id == "TEST_MODEL_V1"
    assert config.config_dir == model_dir


def test_for_model_raises_with_helpful_message_when_missing(tmp_path: Path):
    """Mensaje de error indica dónde se esperaba el archivo y cómo crearlo."""
    with pytest.raises(FileNotFoundError, match="model_configs/inexistente"):
        ModelConfig.for_model("INEXISTENTE", base_dir=tmp_path)


def test_for_model_loads_real_bazboost_v1():
    """Smoke test contra el config real del repo."""
    config = ModelConfig.for_model("BAZBOOST_V1")
    assert config.model_id == "BAZBOOST_V1"
    assert config.primary_target == "b_malo14_26"
    assert len(config.segments) == 11
    assert len(config.targets) == 3


# ---------------------------------------------------------------------------
# Tests de helpers
# ---------------------------------------------------------------------------


def test_score_bin_labels_and_cuts(minimal_config_path: Path):
    config = ModelConfig.from_json_file(minimal_config_path)
    assert config.score_bin_labels == ["0-500", "500-1000"]
    assert config.score_bin_cuts == [0, 500, 1000]


def test_segment_ids_and_segment_id_int(minimal_config_path: Path):
    config = ModelConfig.from_json_file(minimal_config_path)
    assert config.segment_ids == ["s1", "s2"]
    assert config.segment_id_int("s1") == 1
    assert config.segment_id_int("s2") == 2
    with pytest.raises(ValueError, match="segment_id mal formado"):
        config.segment_id_int("xyz")


def test_segment_by_id(minimal_config_path: Path):
    config = ModelConfig.from_json_file(minimal_config_path)
    seg = config.segment_by_id("s1")
    assert seg.feature_count == 2
    assert "edad" in seg.variables
    with pytest.raises(ValueError, match="no existe"):
        config.segment_by_id("s999")


def test_is_categorical(minimal_config_path: Path):
    config = ModelConfig.from_json_file(minimal_config_path)
    assert config.is_categorical("sexo_cat") is True
    assert config.is_categorical("edad") is False
    assert config.is_categorical("no_existe") is False


def test_target_helpers(minimal_config_path: Path):
    config = ModelConfig.from_json_file(minimal_config_path)
    assert config.target_names == ["test_target"]
    target = config.target_by_name("test_target")
    assert target.lag_semanas == 4
    with pytest.raises(ValueError, match="no existe"):
        config.target_by_name("no_existe")


# ---------------------------------------------------------------------------
# Tests de serc_to_canonical
# ---------------------------------------------------------------------------


def test_serc_to_canonical_intercepto_returns_none(minimal_config_path: Path):
    config = ModelConfig.from_json_file(minimal_config_path)
    assert config.serc_to_canonical("INTERCEPTO") is None


def test_serc_to_canonical_uses_name_mapping_for_special_cases(minimal_config_path: Path):
    config = ModelConfig.from_json_file(minimal_config_path)
    assert config.serc_to_canonical("SEXO") == "sexo_cat"
    assert config.serc_to_canonical("INGRESOSESPECIAL") == "ingresos"


def test_serc_to_canonical_fuzzy_match(minimal_config_path: Path):
    """Sin entrada en name_mapping, hace match por uppercase + strip underscores."""
    config = ModelConfig.from_json_file(minimal_config_path)
    assert config.serc_to_canonical("EDAD") == "edad"


def test_serc_to_canonical_unknown_returns_none(minimal_config_path: Path):
    config = ModelConfig.from_json_file(minimal_config_path)
    assert config.serc_to_canonical("VARIABLE_FANTASMA") is None


def test_serc_to_canonical_real_bazboost_v1():
    """Comportamiento equivalente al módulo variable_mapping eliminado."""
    config = ModelConfig.for_model("BAZBOOST_V1")
    assert config.serc_to_canonical("EDAD") == "edad"
    assert config.serc_to_canonical("SEXO") == "fisexo"
    assert config.serc_to_canonical("PORCFCONSTCDC12M") == "porc_f_cons_cdc_12m"
    assert config.serc_to_canonical("INTERCEPTO") is None
    assert config.serc_to_canonical("VARIABLE_INEXISTENTE") is None


# ---------------------------------------------------------------------------
# Iteración 2 A4: ventanas y umbrales de cómputo en ModelConfig
# ---------------------------------------------------------------------------


def test_runtime_params_default_when_absent_in_json(minimal_config_path: Path):
    """Si el JSON no declara los campos A4, caen a defaults conservadores.

    Garantiza backwards-compat: configs viejos (pre-Iter-2) siguen cargando.
    """
    config = ModelConfig.from_json_file(minimal_config_path)
    assert config.psi_window_weeks == 4
    assert config.decile_window_weeks == 4
    assert config.decile_min_obs == 100
    assert config.n_deciles == 10
    assert config.baseline_year == 2026
    assert config.baseline_n_weeks == 4


def test_runtime_params_override_from_json(tmp_path: Path, minimal_config_dict: dict):
    """Si el JSON declara los campos A4, ganan sobre los defaults."""
    minimal_config_dict["psi_window_weeks"] = 8
    minimal_config_dict["decile_window_weeks"] = 6
    minimal_config_dict["decile_min_obs"] = 250
    minimal_config_dict["n_deciles"] = 20
    minimal_config_dict["baseline_year"] = 2025
    minimal_config_dict["baseline_n_weeks"] = 12

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(minimal_config_dict), encoding="utf-8")

    config = ModelConfig.from_json_file(cfg_path)
    assert config.psi_window_weeks == 8
    assert config.decile_window_weeks == 6
    assert config.decile_min_obs == 250
    assert config.n_deciles == 20
    assert config.baseline_year == 2025
    assert config.baseline_n_weeks == 12


def test_runtime_params_real_bazboost_v1():
    """El config real declara los valores A4 explícitos (Iteración 2)."""
    config = ModelConfig.for_model("BAZBOOST_V1")
    assert config.psi_window_weeks == 4
    assert config.decile_window_weeks == 4
    assert config.decile_min_obs == 100
    assert config.n_deciles == 10
    assert config.baseline_year == 2026
    assert config.baseline_n_weeks == 4
