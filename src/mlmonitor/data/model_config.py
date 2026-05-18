"""
ModelConfig — Configuración estática de un modelo a monitorear.

Reemplaza las constantes módulo que vivían en `bootstrap.py` y todo el módulo
`variable_mapping.py`. Cada modelo tiene su configuración en:

    data/inputs/model_configs/<model_id_lowercase>/config.json
    data/inputs/model_configs/<model_id_lowercase>/variable_descriptions.csv
    data/inputs/model_configs/<model_id_lowercase>/segment_descriptions.csv
    data/inputs/model_configs/<model_id_lowercase>/thresholds.csv

Decisión arquitectónica (ver docs/decisions.md §8.2.30):
- JSON sobre Python module / YAML / TOML: separa "datos del modelo" de
  "código de la herramienta", sin dependencias nuevas, portable.
- Versionado en git, baked into la imagen Docker: la config va con el código,
  no se sincroniza desde S3 — evita drift entre versión de código y config remota.

Uso:
    config = ModelConfig.for_model("BAZBOOST_V1")
    bootstrap = ModelBootstrap(session, config=config, ...)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class TargetConfig:
    """Variable target del modelo (outcome a predecir)."""

    name: str                      # ej: "b_malo14_26"
    lag_semanas: int               # ventana de observación
    ascending_order: bool          # True=crece con score, False=decrece (b_malo)


@dataclass(frozen=True)
class SegmentConfig:
    """Sub-modelo / segmento del modelo. BAZBOOST_V1 tiene 11 (s1..s11)."""

    segment_id: str                # ej: "s1"
    group_name: str                # ej: "NO FILES" / "THIN FILES" / "BIG FILES"
    feature_count: int
    variables: list[str]           # nombres canónicos de las variables input


@dataclass
class ModelConfig:
    """
    Configuración completa de un modelo. Cargada desde
    `data/inputs/model_configs/<model_id_lowercase>/config.json`.

    No es frozen porque `config_dir` se setea después de la carga del JSON.
    """

    model_id: str
    model_name: str
    model_type: str
    owner_team: str
    target_definition: str
    score_min: int
    score_max: int
    primary_target: str
    missing_sentinel: int
    num_bins_numeric: int
    score_bins: list[tuple[int, int]]
    categorical_variables: list[str]
    targets: list[TargetConfig]
    segments: list[SegmentConfig]
    name_mapping: dict[str, str]              # SERC → canónico (casos especiales)
    extra_serc_variables: list[str]           # variables a ignorar
    # Ventanas y umbrales de cómputo (Iteración 2 A4). Opcionales: si el JSON
    # no los trae se usan defaults conservadores. Permite que cada modelo
    # declare cadencia y volumen propios sin tocar código.
    psi_window_weeks: int = 4
    decile_window_weeks: int = 4
    decile_min_obs: int = 100
    n_deciles: int = 10
    # Ventana del baseline: primeras N semanas ISO de `baseline_year` dentro
    # de variables_serc_*.csv. Ver ADR §8.2.29.
    baseline_year: int = 2026
    baseline_n_weeks: int = 4
    config_dir: Path = field(default_factory=Path)  # path absoluto, set por loader

    # ------------------------------------------------------------------
    # Paths a CSVs de catálogo (convención fija dentro del config_dir)
    # ------------------------------------------------------------------

    @property
    def variable_descriptions_csv(self) -> Path:
        return self.config_dir / "variable_descriptions.csv"

    @property
    def segment_descriptions_csv(self) -> Path:
        return self.config_dir / "segment_descriptions.csv"

    @property
    def thresholds_csv(self) -> Path:
        return self.config_dir / "thresholds.csv"

    # ------------------------------------------------------------------
    # Derivados de score_bins
    # ------------------------------------------------------------------

    @property
    def score_bin_labels(self) -> list[str]:
        """['0-100', '100-200', ..., '900-1000']."""
        return [f"{lo}-{hi}" for lo, hi in self.score_bins]

    @property
    def score_bin_cuts(self) -> list[int]:
        """Cuts numéricos para pd.cut: [0, 100, 200, ..., 1000]."""
        return [b[0] for b in self.score_bins] + [self.score_bins[-1][1]]

    # ------------------------------------------------------------------
    # Helpers de segmentos
    # ------------------------------------------------------------------

    @property
    def segment_ids(self) -> list[str]:
        return [s.segment_id for s in self.segments]

    def segment_id_int(self, segment_id: str) -> int:
        """'s11' → 11. Útil para CSVs upstream que usan fiidsegmento como int."""
        if not segment_id.startswith("s") or not segment_id[1:].isdigit():
            raise ValueError(f"segment_id mal formado: '{segment_id}' (esperado 's<n>')")
        return int(segment_id[1:])

    def segment_by_id(self, segment_id: str) -> SegmentConfig:
        """Lookup por segment_id; raises ValueError si no existe."""
        for s in self.segments:
            if s.segment_id == segment_id:
                return s
        raise ValueError(
            f"Segmento '{segment_id}' no existe en modelo '{self.model_id}'. "
            f"Activos: {self.segment_ids}"
        )

    # ------------------------------------------------------------------
    # Variables y targets
    # ------------------------------------------------------------------

    def is_categorical(self, variable_name: str) -> bool:
        return variable_name in self.categorical_variables

    @property
    def target_names(self) -> list[str]:
        return [t.name for t in self.targets]

    def target_by_name(self, name: str) -> TargetConfig:
        for t in self.targets:
            if t.name == name:
                return t
        raise ValueError(f"Target '{name}' no existe en modelo '{self.model_id}'")

    # ------------------------------------------------------------------
    # SERC → canónico (reemplaza variable_mapping.serc_to_canonical)
    # ------------------------------------------------------------------

    def serc_to_canonical(self, serc_name: str) -> str | None:
        """
        Convierte un nombre SERC (uppercase, sin separadores) a su forma canónica.

        Returns None si:
        - El nombre es 'INTERCEPTO' (variable artificial del modelo lineal).
        - El nombre no mapea a ninguna variable canónica (es una variable
          intermedia / extra que no se monitorea).
        """
        upper = serc_name.upper()
        if upper == "INTERCEPTO":
            return None
        if upper in self.name_mapping:
            return self.name_mapping[upper]
        key = upper.replace("_", "")
        return self._reverse_canonical_map.get(key)

    @property
    def _reverse_canonical_map(self) -> dict[str, str]:
        """Cache lazy: {NORMALIZADA_SIN_GUIONES_BAJOS: nombre_canónico}.

        Se construye uniendo todas las variables únicas de todos los segmentos.
        Una variable repetida en varios segmentos aparece una sola vez (la
        primera; son siempre la misma). El cache vive en self.__dict__.
        """
        cached = self.__dict__.get("_reverse_canonical_map_cache")
        if cached is not None:
            return cached
        mapping: dict[str, str] = {}
        for s in self.segments:
            for v in s.variables:
                norm = v.upper().replace("_", "")
                if norm not in mapping:
                    mapping[norm] = v
        self.__dict__["_reverse_canonical_map_cache"] = mapping
        return mapping

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------

    @classmethod
    def from_json_file(cls, path: Path) -> "ModelConfig":
        """Carga + valida un archivo config.json."""
        if not path.exists():
            raise FileNotFoundError(f"Config no encontrada: {path}")
        with path.open(encoding="utf-8") as f:
            data = json.load(f)

        # Validar campos requeridos
        required = {
            "model_id", "model_name", "model_type", "owner_team", "target_definition",
            "score_min", "score_max", "primary_target", "missing_sentinel",
            "num_bins_numeric", "score_bins", "categorical_variables",
            "targets", "segments", "name_mapping", "extra_serc_variables",
        }
        missing = required - set(data.keys())
        if missing:
            raise ValueError(
                f"Config {path} le faltan campos requeridos: {sorted(missing)}"
            )

        # Construir dataclasses anidados
        targets = [TargetConfig(**t) for t in data["targets"]]
        segments = [SegmentConfig(**s) for s in data["segments"]]
        score_bins = [tuple(pair) for pair in data["score_bins"]]

        # Validaciones de consistencia
        if not targets:
            raise ValueError(f"Config {path}: 'targets' no puede estar vacío")
        if not segments:
            raise ValueError(f"Config {path}: 'segments' no puede estar vacío")

        target_names = {t.name for t in targets}
        if data["primary_target"] not in target_names:
            raise ValueError(
                f"Config {path}: primary_target='{data['primary_target']}' "
                f"no aparece en targets={sorted(target_names)}"
            )

        all_segment_vars: set[str] = set()
        for s in segments:
            all_segment_vars.update(s.variables)
        for cat_var in data["categorical_variables"]:
            if cat_var not in all_segment_vars:
                raise ValueError(
                    f"Config {path}: categorical_variables incluye '{cat_var}' "
                    f"que no está declarada en ningún segmento"
                )

        for lo, hi in score_bins:
            if hi <= lo:
                raise ValueError(
                    f"Config {path}: score_bin ({lo}, {hi}) inválido (hi debe ser > lo)"
                )

        return cls(
            model_id=data["model_id"],
            model_name=data["model_name"],
            model_type=data["model_type"],
            owner_team=data["owner_team"],
            target_definition=data["target_definition"],
            score_min=int(data["score_min"]),
            score_max=int(data["score_max"]),
            primary_target=data["primary_target"],
            missing_sentinel=int(data["missing_sentinel"]),
            num_bins_numeric=int(data["num_bins_numeric"]),
            score_bins=score_bins,
            categorical_variables=list(data["categorical_variables"]),
            targets=targets,
            segments=segments,
            name_mapping=dict(data["name_mapping"]),
            extra_serc_variables=list(data["extra_serc_variables"]),
            psi_window_weeks=int(data.get("psi_window_weeks", 4)),
            decile_window_weeks=int(data.get("decile_window_weeks", 4)),
            decile_min_obs=int(data.get("decile_min_obs", 100)),
            n_deciles=int(data.get("n_deciles", 10)),
            baseline_year=int(data.get("baseline_year", 2026)),
            baseline_n_weeks=int(data.get("baseline_n_weeks", 4)),
            config_dir=path.parent,
        )

    @classmethod
    def for_model(
        cls,
        model_id: str,
        base_dir: Path | None = None,
    ) -> "ModelConfig":
        """
        Auto-resuelve el path por convención:

            <base_dir>/<model_id_lowercase>/config.json

        base_dir default: <repo_root>/data/inputs/model_configs/
        """
        if base_dir is None:
            # repo_root = .../mlmonitor (donde está pyproject.toml)
            # __file__ = .../mlmonitor/src/mlmonitor/data/model_config.py
            # parents: [data, mlmonitor, src, mlmonitor (root)]
            repo_root = Path(__file__).resolve().parents[3]
            base_dir = repo_root / "data" / "inputs" / "model_configs"
        config_path = Path(base_dir) / model_id.lower() / "config.json"
        if not config_path.exists():
            raise FileNotFoundError(
                f"No existe config.json para model_id={model_id}: esperado en {config_path}. "
                f"Crea el directorio data/inputs/model_configs/{model_id.lower()}/ "
                f"con config.json + variable_descriptions.csv + segment_descriptions.csv + thresholds.csv."
            )
        return cls.from_json_file(config_path)
