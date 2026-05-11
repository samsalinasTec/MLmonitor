"""
ModelBootstrap — Poblacion inicial (una sola vez) de tablas META y distribucion de referencia.

Separa la logica de inicializacion del ETL incremental semanal:
- META_MODEL_REGISTRY: una fila por segmento (los segmentos vienen del config del modelo)
- META_VARIABLES: input + output (score) + target por segmento
- META_METRIC_THRESHOLDS: umbrales por segmento (desde thresholds.csv del modelo)
- META_BASELINE_DISTRIBUTIONS: distribuciones del baseline de entrenamiento (WIDE)

Toda la configuración estática del modelo (variables, segmentos, targets, score_bins,
nombres, tipos de variables categóricas, etc.) vive en
`data/inputs/model_configs/<model_id>/config.json`. Ver `data/model_config.py`
y la ADR §8.2.30.

Uso:
    config = ModelConfig.for_model("BAZBOOST_V1")
    bootstrap = ModelBootstrap(session, config=config)
    result = bootstrap.run()
"""

import logging
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from mlmonitor.data.model_config import ModelConfig
from mlmonitor.db.models import (
    MetaBaselineDistributions,
    MetaMetricThresholds,
    MetaModelRegistry,
    MetaVariables,
)

logger = logging.getLogger(__name__)


def _load_variable_descriptions(csv_path: Path) -> dict[str, str]:
    """Load canonical variable short descriptions desde el CSV indicado.

    Returns {variable_name: short_description}.
    """
    if not csv_path.exists():
        logger.warning("Variable descriptions CSV not found: %s", csv_path)
        return {}
    df = pd.read_csv(csv_path)
    out: dict[str, str] = {}
    for _, row in df.iterrows():
        vname = str(row.get("Variable", "")).strip()
        raw_desc = row.get("Descripción Corta")
        if pd.isna(raw_desc) or str(raw_desc).strip() == "":
            continue
        desc = str(raw_desc).strip()
        if vname and desc:
            out[vname] = desc
    logger.info("Loaded %d variable descriptions from %s", len(out), csv_path.name)
    return out


def _load_segment_descriptions(csv_path: Path) -> dict[int, str]:
    """Load segment short descriptions desde el CSV indicado.

    Returns {segment_id (int): short_description}.
    """
    if not csv_path.exists():
        logger.warning("Segment descriptions CSV not found: %s", csv_path)
        return {}
    df = pd.read_csv(csv_path)
    out: dict[int, str] = {}
    for _, row in df.iterrows():
        try:
            seg_id = int(row["Segmento"])
        except (ValueError, KeyError):
            continue
        desc = str(row.get("Descripción Corta", "")).strip()
        if desc:
            out[seg_id] = desc
    logger.info("Loaded %d segment descriptions from %s", len(out), csv_path.name)
    return out


class ModelBootstrap:
    """Inicializa META tables y distribucion de referencia (una sola vez)."""

    def __init__(
        self,
        session: Session,
        config: ModelConfig,
        raw_dir: str | Path = "data/inputs/raw_tables",
        baseline_filename: str | None = None,
    ):
        self.session = session
        self.config = config
        self.raw_dir = Path(raw_dir)
        self._baseline_filename = baseline_filename

        # Mapas internos poblados durante el bootstrap
        self._registry_map: dict[str, int] = {}      # submodel_id -> surrogate id
        self._variable_map: dict[tuple[str, str], int] = {}  # (submodel_id, var_name) -> var_id
        self._score_var_map: dict[str, int] = {}      # submodel_id -> score variable_id

        # Description lookups from CSVs en el directorio del config del modelo
        self._variable_descriptions = _load_variable_descriptions(config.variable_descriptions_csv)
        self._segment_descriptions = _load_segment_descriptions(config.segment_descriptions_csv)

    def _resolve_baseline_path(self) -> Path:
        """Resolve baseline CSV path: explicit name > glob > fallback."""
        if self._baseline_filename:
            return self.raw_dir / self._baseline_filename
        candidates = sorted(self.raw_dir.glob("base_train_test_bb*.csv"))
        if candidates:
            return candidates[0]
        return self.raw_dir / "base_train_test_bb.csv"

    def run(self) -> dict:
        """Ejecuta bootstrap completo: META + distribuciones baseline.

        Returns:
            Dict con conteos de filas insertadas por tabla.
        """
        counts: dict[str, int] = {}
        counts["META_MODEL_REGISTRY"] = self._populate_meta_model_registry()
        counts["META_VARIABLES"] = self._populate_meta_variables()
        counts["META_METRIC_THRESHOLDS"] = self._populate_meta_metric_thresholds()
        counts["META_BASELINE_DISTRIBUTIONS"] = self._populate_baseline_distributions()
        return counts

    # ------------------------------------------------------------------
    # META tables
    # ------------------------------------------------------------------

    def _populate_meta_model_registry(self) -> int:
        rows = []
        for seg in self.config.segments:
            seg_int = self.config.segment_id_int(seg.segment_id)
            seg_desc = self._segment_descriptions.get(
                seg_int,
                f"Segmento {seg_int} — {seg.group_name}",
            )

            rows.append(MetaModelRegistry(
                model_id=self.config.model_id,
                submodel_id=seg.segment_id,
                model_name=self.config.model_name,
                model_description=seg_desc,
                model_type=self.config.model_type,
                target_definition=self.config.target_definition,
                score_min=self.config.score_min,
                score_max=self.config.score_max,
                feature_count=seg.feature_count,
                primary_target_variable=self.config.primary_target,
                training_cutoff_date=None,
                owner_team=self.config.owner_team,
                valid_from=date(2024, 3, 1),
                valid_to=None,
            ))

        self.session.add_all(rows)
        self.session.flush()
        self._registry_map = {r.submodel_id: r.id for r in rows}
        logger.info("META_MODEL_REGISTRY: %d rows", len(rows))
        return len(rows)

    def _populate_meta_variables(self) -> int:
        rows = []
        for seg in self.config.segments:
            reg_id = self._registry_map[seg.segment_id]

            # Variables de input
            for vname in seg.variables:
                vtype = "categorical" if self.config.is_categorical(vname) else "numeric"
                var_desc = self._variable_descriptions.get(vname)
                if var_desc is None:
                    logger.warning(
                        "No description found for variable '%s' (segment %s)",
                        vname, seg.segment_id,
                    )
                rows.append(MetaVariables(
                    model_registry_id=reg_id,
                    variable_name=vname,
                    variable_type=vtype,
                    variable_rol="input",
                    lag_semanas=None,
                    ascending_order=None,
                    description=var_desc,
                    woe_categories=None,
                    binning_rules=(
                        {"type": "quantile", "n_bins": self.config.num_bins_numeric}
                        if vtype == "numeric" else None
                    ),
                    source_table=None,
                    valid_from=date(2023, 1, 1),
                    valid_to=None,
                ))

            # Variable de output: score (con score_bin_cuts del config persistidos)
            rows.append(MetaVariables(
                model_registry_id=reg_id,
                variable_name="score",
                variable_type="numeric",
                variable_rol="output",
                lag_semanas=None,
                ascending_order=None,
                description="Puntaje total del scorecard",
                woe_categories=None,
                binning_rules={"type": "fixed_cuts", "cuts": self.config.score_bin_cuts},
                source_table=None,
                valid_from=date(2023, 1, 1),
                valid_to=None,
            ))

            # Variables target
            for target in self.config.targets:
                rows.append(MetaVariables(
                    model_registry_id=reg_id,
                    variable_name=target.name,
                    variable_type="numeric",
                    variable_rol="target",
                    lag_semanas=target.lag_semanas,
                    ascending_order=target.ascending_order,
                    description=None,
                    woe_categories=None,
                    binning_rules=None,
                    source_table=None,
                    valid_from=date(2023, 1, 1),
                    valid_to=None,
                ))

        self.session.add_all(rows)
        self.session.flush()

        for r in rows:
            submodel_id = next(k for k, v in self._registry_map.items() if v == r.model_registry_id)
            if r.variable_rol == "output" and r.variable_name == "score":
                self._score_var_map[submodel_id] = r.id
            elif r.variable_rol == "input":
                self._variable_map[(submodel_id, r.variable_name)] = r.id

        logger.info("META_VARIABLES: %d rows", len(rows))
        return len(rows)

    def _populate_meta_metric_thresholds(self) -> int:
        """Persiste thresholds per-segmento desde el CSV de crédito.

        Una fila por (segmento, métrica). Si el CSV no trae una métrica
        esperada, se usa el default de `threshold_loader`. Variables intermedias
        del CSV (EXTRA_SERC) y métricas sobrantes se ignoran. La `direction`
        se aplica desde la regla canónica, no desde el CSV. Ver ADR §8.2.23.
        """
        from mlmonitor.data.threshold_loader import (
            compute_thresholds_for_segment,
            parse_thresholds_csv,
        )

        csv_lookup = parse_thresholds_csv(self.config.thresholds_csv, self.config)

        rows: list[MetaMetricThresholds] = []
        for submodel_id, registry_id in self._registry_map.items():
            for kwargs in compute_thresholds_for_segment(
                submodel_id, registry_id, csv_lookup, self.config,
            ):
                rows.append(MetaMetricThresholds(
                    **kwargs,
                    valid_from=date(2025, 1, 1),
                    valid_to=None,
                ))
        self.session.add_all(rows)
        self.session.flush()
        logger.info("META_METRIC_THRESHOLDS: %d rows", len(rows))
        return len(rows)

    # ------------------------------------------------------------------
    # Distribuciones de referencia (META_BASELINE_DISTRIBUTIONS)
    # ------------------------------------------------------------------

    def _populate_baseline_distributions(self) -> int:
        """Calcula bins y distribuciones desde el baseline de entrenamiento (WIDE).

        El baseline es un CSV con formato WIDE: una fila por credito,
        variables canonicas como columnas directas.  Contrasta con
        variables_serc (LONG).

        - Numericas: qcut sobre baseline → fixed_cuts en META_VARIABLES.binning_rules
        - Categoricas: categorias directas → META_VARIABLES.woe_categories
        - Score: bins fijos de SCORE_BIN_CUTS (ya en META_VARIABLES)
        - Distribuciones → META_BASELINE_DISTRIBUTIONS
        """
        baseline_path = self._resolve_baseline_path()

        if not baseline_path.exists():
            logger.warning("Baseline file not found: %s — skipping baseline distributions", baseline_path)
            return 0

        logger.info("Loading baseline %s for reference distributions", baseline_path)
        baseline_df = pd.read_csv(baseline_path, low_memory=False)
        logger.info("Baseline shape: %s", baseline_df.shape)

        total_rows = 0
        total_rows += self._baseline_variable_distributions(baseline_df)
        total_rows += self._baseline_score_distributions(baseline_df)
        return total_rows

    def _baseline_variable_distributions(self, baseline_df: pd.DataFrame) -> int:
        """Calcula y persiste distribucion baseline para variables input."""
        all_rows: list[MetaBaselineDistributions] = []

        for seg in self.config.segments:
            reg_id = self._registry_map.get(seg.segment_id)
            if reg_id is None:
                continue

            seg_int = self.config.segment_id_int(seg.segment_id)
            seg_df = baseline_df[baseline_df["fiidsegmento"] == seg_int]
            if seg_df.empty:
                logger.warning("No baseline data for segment %s", seg.segment_id)
                continue

            for vname in seg.variables:
                var_id = self._variable_map.get((seg.segment_id, vname))
                if var_id is None:
                    continue

                if vname not in seg_df.columns:
                    logger.warning(
                        "Variable '%s' not found in baseline columns (segment %s)",
                        vname, seg.segment_id,
                    )
                    continue

                if self.config.is_categorical(vname):
                    all_rows.extend(self._bin_categorical_baseline(
                        seg_df[vname], reg_id, var_id,
                    ))
                else:
                    all_rows.extend(self._bin_numeric_baseline(
                        seg_df[vname], reg_id, var_id,
                    ))

        if all_rows:
            self.session.add_all(all_rows)
            self.session.flush()

        logger.info("META_BASELINE_DISTRIBUTIONS (variables): %d rows", len(all_rows))
        return len(all_rows)

    def _bin_numeric_baseline(
        self, values: pd.Series, reg_id: int, var_id: int,
    ) -> list[MetaBaselineDistributions]:
        """Calcula quantile bins desde baseline, persiste cuts, retorna distribucion.

        bin_percentage se calcula como bin_count / len(clean) y se guarda
        redundante junto con bin_count y total_records para evitar el computo
        en cada query de PSI.  Ambos se calculan aqui y nunca se actualizan
        por separado.
        """
        sentinel = self.config.missing_sentinel
        n_bins_target = self.config.num_bins_numeric
        total_records = len(values)
        null_count = int(values.isna().sum() + (values == sentinel).sum())
        clean = values[(values.notna()) & (values != sentinel)]

        if clean.empty:
            return []

        try:
            _, bin_edges = pd.qcut(clean, q=n_bins_target, retbins=True, duplicates="drop")
        except ValueError:
            _, bin_edges = pd.cut(clean, bins=n_bins_target, retbins=True)

        cuts = [float(e) for e in bin_edges]
        var_row = self.session.get(MetaVariables, var_id)
        if var_row is not None:
            var_row.binning_rules = {"type": "fixed_cuts", "cuts": cuts}

        n_bins = len(bin_edges) - 1
        bin_indices = np.digitize(clean.values, bin_edges[1:-1])

        rows = []
        for bin_idx in range(n_bins):
            label = f"bin_{bin_idx + 1}"
            count = int((bin_indices == bin_idx).sum())
            pct = count / len(clean) if len(clean) > 0 else 0.0

            rows.append(MetaBaselineDistributions(
                model_registry_id=reg_id,
                variable_id=var_id,
                bin_label=label,
                bin_count=count,
                bin_percentage=round(pct, 6),
                null_count=null_count if bin_idx == 0 else 0,
                total_records=total_records,
            ))
        return rows

    def _bin_categorical_baseline(
        self, values: pd.Series, reg_id: int, var_id: int,
    ) -> list[MetaBaselineDistributions]:
        """Calcula categorias desde baseline, persiste en META_VARIABLES.

        bin_percentage = bin_count / total_records (redundante; ver docstring
        de _bin_numeric_baseline).
        """
        vals = values.dropna().astype(str)
        ref_categories = list(vals.value_counts().index)

        var_row = self.session.get(MetaVariables, var_id)
        if var_row is not None:
            var_row.woe_categories = ref_categories

        total_records = len(values)
        null_count = int(values.isna().sum())

        rows = []
        for cat_val in ref_categories:
            count = int((vals == cat_val).sum())
            pct = count / total_records if total_records > 0 else 0.0
            rows.append(MetaBaselineDistributions(
                model_registry_id=reg_id,
                variable_id=var_id,
                bin_label=str(cat_val),
                bin_count=count,
                bin_percentage=round(pct, 6),
                null_count=null_count,
                total_records=total_records,
            ))
        return rows

    def _baseline_score_distributions(self, baseline_df: pd.DataFrame) -> int:
        """Distribucion baseline del score total por segmento."""
        score_df = baseline_df.dropna(subset=["fnpuntaje"])

        all_rows: list[MetaBaselineDistributions] = []
        score_bins = self.config.score_bins
        score_bin_labels = self.config.score_bin_labels
        last_idx = len(score_bins) - 1

        for seg in self.config.segments:
            score_var_id = self._score_var_map.get(seg.segment_id)
            reg_id = self._registry_map.get(seg.segment_id)
            if score_var_id is None or reg_id is None:
                continue

            seg_int = self.config.segment_id_int(seg.segment_id)
            grp = score_df[score_df["fiidsegmento"] == seg_int]
            if grp.empty:
                continue

            total_records = len(grp)
            scores = grp["fnpuntaje"]

            for idx, ((lo, hi), label) in enumerate(zip(score_bins, score_bin_labels)):
                if idx == last_idx:
                    in_bin = scores[(scores >= lo) & (scores <= hi)]
                else:
                    in_bin = scores[(scores >= lo) & (scores < hi)]
                count = len(in_bin)
                pct = count / total_records if total_records > 0 else 0.0

                all_rows.append(MetaBaselineDistributions(
                    model_registry_id=reg_id,
                    variable_id=score_var_id,
                    bin_label=label,
                    bin_count=count,
                    bin_percentage=round(pct, 6),
                    null_count=0,
                    total_records=total_records,
                ))

        if all_rows:
            self.session.add_all(all_rows)
            self.session.flush()

        logger.info("META_BASELINE_DISTRIBUTIONS (scores): %d rows", len(all_rows))
        return len(all_rows)
