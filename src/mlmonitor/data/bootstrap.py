"""
ModelBootstrap — Poblacion inicial (una sola vez) de tablas META y distribucion de referencia.

Separa la logica de inicializacion del ETL incremental semanal:
- META_MODEL_REGISTRY: 11 segmentos BAZBOOST_V1
- META_VARIABLES: input + output (score) + target por segmento
- META_METRIC_THRESHOLDS: umbrales globales de alerta
- META_BASELINE_DISTRIBUTIONS: distribuciones del baseline de entrenamiento (WIDE)

Uso:
    bootstrap = ModelBootstrap(session, raw_dir=Path("data/inputs/raw_tables"))
    result = bootstrap.run()
"""

import logging
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from mlmonitor.data.variable_mapping import (
    CANONICAL_VARIABLES,
    SEGMENT_FEATURE_COUNTS,
    SEGMENT_GROUP_NAMES,
)
from mlmonitor.db.models import (
    MetaBaselineDistributions,
    MetaMetricThresholds,
    MetaModelRegistry,
    MetaVariables,
)

logger = logging.getLogger(__name__)

# --- Constantes del modelo BazBoost (fuente de verdad para bootstrap) --------

MODEL_ID = "BAZBOOST_V1"
MODEL_NAME = "BazBoost Credito"
MODEL_TYPE = "logistic_regression_scorecard"
OWNER_TEAM = "analytics credito"

SCORE_BINS = [
    (0, 100), (100, 200), (200, 300), (300, 400), (400, 500),
    (500, 600), (600, 700), (700, 800), (800, 900), (900, 1000),
]
SCORE_BIN_LABELS = [f"{lo}-{hi}" for lo, hi in SCORE_BINS]
SCORE_BIN_CUTS = [b[0] for b in SCORE_BINS] + [SCORE_BINS[-1][1]]

MISSING_SENTINEL = -100
NUM_BINS_NUMERIC = 10

TARGET_VARIABLES: dict[str, dict] = {
    "b_malo4_6":   {"lag_semanas": 6,  "ascending_order": False},
    "b_malo8_13":  {"lag_semanas": 13, "ascending_order": False},
    "b_malo14_26": {"lag_semanas": 26, "ascending_order": False},
}

PRIMARY_TARGET = "b_malo14_26"


def _load_variable_descriptions(raw_dir: Path) -> dict[str, str]:
    """Load canonical variable short descriptions from Dicionario_Variables_BB.csv.

    Returns {variable_name: short_description}.
    """
    csv_path = raw_dir / "Dicionario_Variables_BB.csv"
    if not csv_path.exists():
        logger.warning("Variable dictionary not found: %s", csv_path)
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


def _load_segment_descriptions(raw_dir: Path) -> dict[int, str]:
    """Load segment short descriptions from Dicionario_Segmentos_BB.csv.

    Returns {segment_id (int): short_description}.
    """
    csv_path = raw_dir / "Dicionario_Segmentos_BB.csv"
    if not csv_path.exists():
        logger.warning("Segment dictionary not found: %s", csv_path)
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
        raw_dir: str | Path = "data/inputs/raw_tables",
        baseline_filename: str | None = None,
    ):
        self.session = session
        self.raw_dir = Path(raw_dir)
        self._baseline_filename = baseline_filename

        # Mapas internos poblados durante el bootstrap
        self._registry_map: dict[str, int] = {}      # submodel_id -> surrogate id
        self._variable_map: dict[tuple[str, str], int] = {}  # (submodel_id, var_name) -> var_id
        self._score_var_map: dict[str, int] = {}      # submodel_id -> score variable_id

        # Description lookups from CSV dictionaries
        self._variable_descriptions = _load_variable_descriptions(self.raw_dir)
        self._segment_descriptions = _load_segment_descriptions(self.raw_dir)

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
        for seg_id in range(1, 12):
            submodel_id = f"s{seg_id}"
            group_name = SEGMENT_GROUP_NAMES.get(seg_id, "")
            feature_count = SEGMENT_FEATURE_COUNTS.get(seg_id)
            seg_desc = self._segment_descriptions.get(seg_id, f"Segmento {seg_id} — {group_name}")

            rows.append(MetaModelRegistry(
                model_id=MODEL_ID,
                submodel_id=submodel_id,
                model_name=MODEL_NAME,
                model_description=seg_desc,
                model_type=MODEL_TYPE,
                target_definition="Probabilidad de incumplimiento",
                score_min=0,
                score_max=1000,
                feature_count=feature_count,
                training_cutoff_date=None,
                owner_team=OWNER_TEAM,
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
        for seg_id in range(1, 12):
            submodel_id = f"s{seg_id}"
            reg_id = self._registry_map[submodel_id]
            canonical_vars = CANONICAL_VARIABLES.get(seg_id, [])

            # Variables de input
            for vname in canonical_vars:
                vtype = "categorical" if vname == "fisexo" else "numeric"
                var_desc = self._variable_descriptions.get(vname)
                if var_desc is None:
                    logger.warning("No description found for variable '%s' (segment %d)", vname, seg_id)
                rows.append(MetaVariables(
                    model_registry_id=reg_id,
                    variable_name=vname,
                    variable_type=vtype,
                    variable_rol="input",
                    lag_semanas=None,
                    ascending_order=None,
                    description=var_desc,
                    woe_categories=None,
                    binning_rules={"type": "quantile", "n_bins": NUM_BINS_NUMERIC} if vtype == "numeric" else None,
                    source_table=None,
                    valid_from=date(2023, 1, 1),
                    valid_to=None,
                ))

            # Variable de output: score (con SCORE_BINS persistidos)
            rows.append(MetaVariables(
                model_registry_id=reg_id,
                variable_name="score",
                variable_type="numeric",
                variable_rol="output",
                lag_semanas=None,
                ascending_order=None,
                description="Puntaje total del scorecard",
                woe_categories=None,
                binning_rules={"type": "fixed_cuts", "cuts": SCORE_BIN_CUTS},
                source_table=None,
                valid_from=date(2023, 1, 1),
                valid_to=None,
            ))

            # Variables target
            for tname, tparams in TARGET_VARIABLES.items():
                rows.append(MetaVariables(
                    model_registry_id=reg_id,
                    variable_name=tname,
                    variable_type="numeric",
                    variable_rol="target",
                    lag_semanas=tparams["lag_semanas"],
                    ascending_order=tparams["ascending_order"],
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

        csv_path = self.raw_dir / "tresholds_monitoreo.csv"
        csv_lookup = parse_thresholds_csv(csv_path)

        rows: list[MetaMetricThresholds] = []
        for submodel_id, registry_id in self._registry_map.items():
            for kwargs in compute_thresholds_for_segment(submodel_id, registry_id, csv_lookup):
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

        for seg_id in range(1, 12):
            submodel_id = f"s{seg_id}"
            reg_id = self._registry_map.get(submodel_id)
            if reg_id is None:
                continue

            seg_df = baseline_df[baseline_df["fiidsegmento"] == seg_id]
            if seg_df.empty:
                logger.warning("No baseline data for segment %d", seg_id)
                continue

            canonical_vars = CANONICAL_VARIABLES.get(seg_id, [])

            for vname in canonical_vars:
                var_id = self._variable_map.get((submodel_id, vname))
                if var_id is None:
                    continue

                if vname not in seg_df.columns:
                    logger.warning("Variable '%s' not found in baseline columns (segment %d)", vname, seg_id)
                    continue

                is_categorical = vname == "fisexo"

                if is_categorical:
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
        total_records = len(values)
        null_count = int(values.isna().sum() + (values == MISSING_SENTINEL).sum())
        clean = values[(values.notna()) & (values != MISSING_SENTINEL)]

        if clean.empty:
            return []

        try:
            _, bin_edges = pd.qcut(clean, q=NUM_BINS_NUMERIC, retbins=True, duplicates="drop")
        except ValueError:
            _, bin_edges = pd.cut(clean, bins=NUM_BINS_NUMERIC, retbins=True)

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

        for seg_id in range(1, 12):
            submodel_id = f"s{seg_id}"
            score_var_id = self._score_var_map.get(submodel_id)
            reg_id = self._registry_map.get(submodel_id)
            if score_var_id is None or reg_id is None:
                continue

            grp = score_df[score_df["fiidsegmento"] == seg_id]
            if grp.empty:
                continue

            total_records = len(grp)
            scores = grp["fnpuntaje"]

            last_idx = len(SCORE_BINS) - 1
            for idx, ((lo, hi), label) in enumerate(zip(SCORE_BINS, SCORE_BIN_LABELS)):
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
