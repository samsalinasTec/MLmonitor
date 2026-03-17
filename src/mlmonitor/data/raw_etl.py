"""
RawDataETL — Popula las tablas del star schema desde archivos raw del área de crédito.

Fuentes:
- variables_serc.csv: detalle de variables por solicitud de score
- muestra_weekly.csv: solicitudes con outcomes y score
- Variables_por_segmento.xlsx: catálogo canónico de variables del scorecard
- MetaModelRegistry.xlsx: metadata de los 11 segmentos

Uso:
    etl = RawDataETL(session, raw_dir="data/inputs/raw_tables", transform_dir="data/Transform")
    counts = etl.run()
"""

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from mlmonitor.data.variable_mapping import (
    CANONICAL_VARIABLES,
    SEGMENT_FEATURE_COUNTS,
    SEGMENT_GROUP_NAMES,
    serc_to_canonical,
)
from mlmonitor.db.models import (
    FactDistributions,
    FactPerformanceOutcomes,
    MetaMetricThresholds,
    MetaModelRegistry,
    MetaVariables,
)

logger = logging.getLogger(__name__)

MODEL_ID = "BAZBOOST_V1"
MODEL_NAME = "BazBoost Crédito"
MODEL_TYPE = "logistic_regression_scorecard"
OWNER_TEAM = "analytics credito"

SCORE_BINS = [
    (0, 100), (100, 200), (200, 300), (300, 400), (400, 500),
    (500, 600), (600, 700), (700, 800), (800, 900), (900, 1000),
]
SCORE_BIN_LABELS = [f"{lo}-{hi}" for lo, hi in SCORE_BINS]
SCORE_MIDPOINTS = [(lo + hi) // 2 for lo, hi in SCORE_BINS]

MISSING_SENTINEL = -100
NUM_BINS_NUMERIC = 10

B_MALO_ACTIVE = ['b_malo2_4', 'b_malo4_6', 'b_malo8_13', 'b_malo8_16', 'first_payment_default2']


def _semana_to_date(semana_num: int) -> date:
    """Convierte semana ISO (ej: 202541) al inicio de periodo W-MON (martes)."""
    year, week = divmod(semana_num, 100)
    iso_monday = date.fromisocalendar(year, week, 1)  # lunes ISO
    return iso_monday - timedelta(days=6)              # martes = start W-MON


class RawDataETL:
    def __init__(
        self,
        session: Session,
        raw_dir: str | Path = "data/inputs/raw_tables",
        transform_dir: str | Path = "data/Transform",
    ):
        self.session = session
        self.raw_dir = Path(raw_dir)
        self.transform_dir = Path(transform_dir)

        self._registry_map: dict[str, int] = {}   # submodel_id -> surrogate id
        self._variable_map: dict[tuple[str, str], int] = {}  # (submodel_id, var_name) -> variable_id
        self._score_var_map: dict[str, int] = {}   # submodel_id -> score variable_id
        self._metric_map: dict[str, int] = {}      # metric_name -> metric_id

        self._serc_df: pd.DataFrame | None = None
        self._weekly_df: pd.DataFrame | None = None

    def run(self) -> dict[str, int]:
        """Execute the full ETL. Returns row counts per table."""
        self._load_raw_files()

        counts: dict[str, int] = {}
        counts["META_MODEL_REGISTRY"] = self._populate_meta_model_registry()
        counts["META_VARIABLES"] = self._populate_meta_variables()
        counts["META_METRIC_THRESHOLDS"] = self._populate_meta_metric_thresholds()
        counts["FACT_DISTRIBUTIONS"] = self._populate_fact_distributions()
        counts["FACT_PERFORMANCE_OUTCOMES"] = self._populate_fact_performance_outcomes()
        return counts

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_raw_files(self) -> None:
        serc_path = self.raw_dir / "variables_serc.csv"
        weekly_path = self.raw_dir / "muestra_weekly_S32_S41.csv"

        logger.info("Loading %s", serc_path)
        self._serc_df = pd.read_csv(serc_path)
        self._serc_df["_ts"] = pd.to_datetime(
            self._serc_df["fdregistro_solicitud"], unit="ms", errors="coerce"
        )
        self._serc_df["_reference_week"] = (
            self._serc_df["_ts"]
            .dt.to_period("W-MON")
            .apply(lambda p: p.start_time.date() if pd.notna(p) else None)
        )

        logger.info("Loading %s", weekly_path)
        self._weekly_df = pd.read_csv(weekly_path)

    # ------------------------------------------------------------------
    # META tables
    # ------------------------------------------------------------------

    def _populate_meta_model_registry(self) -> int:
        rows = []
        for seg_id in range(1, 12):
            submodel_id = f"s{seg_id}"
            group_name = SEGMENT_GROUP_NAMES.get(seg_id, "")
            feature_count = SEGMENT_FEATURE_COUNTS.get(seg_id)

            rows.append(MetaModelRegistry(
                model_id=MODEL_ID,
                submodel_id=submodel_id,
                model_name=MODEL_NAME,
                model_description=f"Segmento {seg_id} — {group_name}",
                model_type=MODEL_TYPE,
                target_definition="Probabilidad de incumplimiento",
                score_min=0,
                score_max=1000,
                lag_semanas=None,
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

            for vname in canonical_vars:
                vtype = "categorical" if vname == "fisexo" else "numeric"
                rows.append(MetaVariables(
                    model_registry_id=reg_id,
                    variable_name=vname,
                    variable_type=vtype,
                    variable_rol="input",
                    description=None,
                    woe_categories=None,
                    binning_rules={"type": "quantile", "n_bins": NUM_BINS_NUMERIC} if vtype == "numeric" else None,
                    source_table=None,
                    valid_from=date(2023, 1, 1),
                    valid_to=None,
                ))

            rows.append(MetaVariables(
                model_registry_id=reg_id,
                variable_name="score",
                variable_type="numeric",
                variable_rol="output",
                description="Puntaje total del scorecard",
                woe_categories=None,
                binning_rules={"type": "fixed_cuts", "cuts": [0, 100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]},
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
            else:
                self._variable_map[(submodel_id, r.variable_name)] = r.id

        logger.info("META_VARIABLES: %d rows", len(rows))
        return len(rows)

    def _populate_meta_metric_thresholds(self) -> int:
        global_thresholds = [
            ("psi", 0.10, 0.20, "higher_worse"),
            ("gini", 0.35, 0.25, "lower_worse"),
            ("ks", 0.20, 0.15, "lower_worse"),
            ("roll_forward_ordering_violations", 1, 2, "higher_worse"),
            ("payment_rate_ordering_violations", 1, 2, "higher_worse"),
            ("null_rate", 0.03, 0.10, "higher_worse"),
        ]
        rows = []
        for metric, warn, crit, direction in global_thresholds:
            rows.append(MetaMetricThresholds(
                metric_name=metric,
                model_registry_id=None,
                warning_threshold=warn,
                critical_threshold=crit,
                direction=direction,
                valid_from=date(2025, 1, 1),
                valid_to=None,
            ))
        self.session.add_all(rows)
        self.session.flush()
        self._metric_map = {r.metric_name: r.id for r in rows}
        logger.info("META_METRIC_THRESHOLDS: %d rows", len(rows))
        return len(rows)

    # ------------------------------------------------------------------
    # FACT_DISTRIBUTIONS
    # ------------------------------------------------------------------

    def _populate_fact_distributions(self) -> int:
        if self._serc_df is None or self._serc_df.empty:
            logger.warning("No SERC data loaded, skipping distributions")
            return 0

        total_rows = 0
        total_rows += self._insert_variable_distributions()
        total_rows += self._insert_score_distributions()
        return total_rows

    def _insert_variable_distributions(self) -> int:
        """Bin fcvalor_variable per (segment, variable, week) and insert."""
        df = self._serc_df.copy()

        df["_canonical"] = df["fcnombre_variable"].apply(serc_to_canonical)
        df = df.dropna(subset=["_canonical", "_reference_week"])

        df["fcvalor_variable"] = pd.to_numeric(df["fcvalor_variable"], errors="coerce")

        all_rows: list[FactDistributions] = []

        for (seg_id, canonical), grp in df.groupby(["fiidsegmento", "_canonical"]):
            submodel_id = f"s{seg_id}"
            var_id = self._variable_map.get((submodel_id, canonical))
            reg_id = self._registry_map.get(submodel_id)
            if var_id is None or reg_id is None:
                continue

            is_categorical = canonical == "fisexo"

            if is_categorical:
                all_rows.extend(self._bin_categorical(grp, reg_id, var_id))
            else:
                all_rows.extend(self._bin_numeric(grp, reg_id, var_id))

        if all_rows:
            self.session.add_all(all_rows)
            self.session.flush()

        logger.info("FACT_DISTRIBUTIONS (variables): %d rows", len(all_rows))
        return len(all_rows)

    def _bin_numeric(
        self, grp: pd.DataFrame, reg_id: int, var_id: int
    ) -> list[FactDistributions]:
        """Bin a numeric variable group by week using quantile bins.

        Bin edges are computed from the full dataset (all weeks) so PSI
        comparisons across weeks use a consistent reference frame.
        Labels use ``bin_1`` .. ``bin_N`` to guarantee uniqueness.
        """
        rows = []
        valid_values = grp.loc[
            grp["fcvalor_variable"].notna() & (grp["fcvalor_variable"] != MISSING_SENTINEL),
            "fcvalor_variable",
        ]

        if valid_values.empty:
            return rows

        try:
            _, bin_edges = pd.qcut(valid_values, q=NUM_BINS_NUMERIC, retbins=True, duplicates="drop")
        except ValueError:
            _, bin_edges = pd.cut(valid_values, bins=NUM_BINS_NUMERIC, retbins=True)

        n_bins = len(bin_edges) - 1
        first_week = grp["_reference_week"].dropna().min()

        for ref_week, week_grp in grp.groupby("_reference_week"):
            vals = week_grp["fcvalor_variable"]
            total_records = len(vals)
            null_count = int(vals.isna().sum() + (vals == MISSING_SENTINEL).sum())
            clean = vals[(vals.notna()) & (vals != MISSING_SENTINEL)]

            if clean.empty:
                continue

            bin_indices = np.digitize(clean.values, bin_edges[1:-1])
            reference_flag = 1 if ref_week == first_week else 0

            for bin_idx in range(n_bins):
                label = f"bin_{bin_idx + 1}"
                count = int((bin_indices == bin_idx).sum())
                pct = count / len(clean) if len(clean) > 0 else 0.0

                rows.append(FactDistributions(
                    model_registry_id=reg_id,
                    variable_id=var_id,
                    reference_week=ref_week,
                    reference_flag=reference_flag,
                    bin_label=label,
                    bin_count=count,
                    bin_percentage=round(pct, 6),
                    null_count=null_count if bin_idx == 0 else 0,
                    sum_value=None,
                    total_records=total_records,
                ))
        return rows

    def _bin_categorical(
        self, grp: pd.DataFrame, reg_id: int, var_id: int
    ) -> list[FactDistributions]:
        """Bin a categorical variable by its distinct values per week."""
        rows = []
        first_week = grp["_reference_week"].dropna().min()
        for ref_week, week_grp in grp.groupby("_reference_week"):
            vals = week_grp["fcvalor_variable"].astype(str)
            total_records = len(vals)
            null_count = int(week_grp["fcvalor_variable"].isna().sum())
            counts = vals.value_counts()
            reference_flag = 1 if ref_week == first_week else 0

            for cat_val, count in counts.items():
                pct = count / total_records if total_records > 0 else 0.0
                rows.append(FactDistributions(
                    model_registry_id=reg_id,
                    variable_id=var_id,
                    reference_week=ref_week,
                    reference_flag=reference_flag,
                    bin_label=str(cat_val),
                    bin_count=int(count),
                    bin_percentage=round(pct, 6),
                    null_count=null_count,
                    sum_value=None,
                    total_records=total_records,
                ))
        return rows

    def _insert_score_distributions(self) -> int:
        """Bin fnpuntaje (total score) per segment per week."""
        df = self._serc_df
        if df is None:
            return 0

        score_df = (
            df.groupby("fiidscoreds")
            .agg(
                fiidsegmento=("fiidsegmento", "first"),
                fnpuntaje=("fnpuntaje", "first"),
                _reference_week=("_reference_week", "first"),
            )
            .reset_index()
        )
        score_df = score_df.dropna(subset=["_reference_week", "fnpuntaje"])

        all_rows: list[FactDistributions] = []

        # Pre-compute first week per segment for reference_flag
        first_week_by_seg: dict[int, date] = {}
        for seg_id, seg_grp in score_df.groupby("fiidsegmento"):
            first_week_by_seg[seg_id] = seg_grp["_reference_week"].dropna().min()

        for (seg_id, ref_week), grp in score_df.groupby(["fiidsegmento", "_reference_week"]):
            submodel_id = f"s{seg_id}"
            score_var_id = self._score_var_map.get(submodel_id)
            reg_id = self._registry_map.get(submodel_id)
            if score_var_id is None or reg_id is None:
                continue

            total_records = len(grp)
            scores = grp["fnpuntaje"]
            reference_flag = 1 if ref_week == first_week_by_seg.get(seg_id) else 0

            for (lo, hi), label, midpoint in zip(SCORE_BINS, SCORE_BIN_LABELS, SCORE_MIDPOINTS):
                in_bin = scores[(scores >= lo) & (scores < hi)]
                count = len(in_bin)
                pct = count / total_records if total_records > 0 else 0.0

                all_rows.append(FactDistributions(
                    model_registry_id=reg_id,
                    variable_id=score_var_id,
                    reference_week=ref_week,
                    reference_flag=reference_flag,
                    bin_label=label,
                    bin_count=count,
                    bin_percentage=round(pct, 6),
                    null_count=0,
                    sum_value=float(in_bin.sum()) if count > 0 else 0.0,
                    total_records=total_records,
                ))

        if all_rows:
            self.session.add_all(all_rows)
            self.session.flush()

        logger.info("FACT_DISTRIBUTIONS (scores): %d rows", len(all_rows))
        return len(all_rows)

    # ------------------------------------------------------------------
    # FACT_PERFORMANCE_OUTCOMES
    # ------------------------------------------------------------------

    def _populate_fact_performance_outcomes(self) -> int:
        if self._weekly_df is None or self._weekly_df.empty:
            logger.warning("No weekly data loaded, skipping performance outcomes")
            return 0

        df = self._weekly_df.copy()

        df = df[df["flg_baz_boost"] == 1].copy()
        if df.empty:
            logger.warning("No BazBoost records (flg_baz_boost=1) found")
            return 0

        df = df[df["flg_surtida"] == 1].copy()
        if df.empty:
            logger.warning("No disbursed records (flg_surtida=1)")
            return 0

        logger.info("Performance outcomes: %d records (flg_baz_boost=1, flg_surtida=1)", len(df))

        df["_score_bin"] = pd.cut(
            df["fnpuntaje"],
            bins=[b[0] for b in SCORE_BINS] + [SCORE_BINS[-1][1]],
            labels=SCORE_BIN_LABELS,
            right=False,
        )
        df["_midpoint"] = df["_score_bin"].map(
            dict(zip(SCORE_BIN_LABELS, SCORE_MIDPOINTS))
        )

        all_rows: list[FactPerformanceOutcomes] = []

        for (seg_id, semana_num), grp in df.groupby(["fiidsegmento", "semana_num"]):
            submodel_id = f"s{seg_id}"
            reg_id = self._registry_map.get(submodel_id)
            if reg_id is None:
                continue

            date_score_key = _semana_to_date(int(semana_num))

            for score_bin, bin_grp in grp.groupby("_score_bin", observed=True):
                count_total = len(bin_grp)
                midpoint = bin_grp["_midpoint"].iloc[0] if len(bin_grp) > 0 else None

                for bmalo_col in B_MALO_ACTIVE:
                    if bmalo_col not in bin_grp.columns:
                        continue
                    valid = bin_grp[bmalo_col].dropna()
                    count_event_real = int(valid.sum()) if not valid.empty else 0

                    all_rows.append(FactPerformanceOutcomes(
                        model_registry_id=reg_id,
                        date_score_key=date_score_key,
                        date_outcome_key=date_score_key,
                        metric_type=bmalo_col,
                        score_bin=str(score_bin),
                        score_midpoint=int(midpoint) if midpoint is not None else None,
                        count_total=count_total,
                        count_event_real=count_event_real,
                        sum_predicted_score=float(bin_grp["fnpuntaje"].sum()),
                    ))

        if all_rows:
            self.session.add_all(all_rows)
            self.session.flush()

        logger.info("FACT_PERFORMANCE_OUTCOMES: %d rows", len(all_rows))
        return len(all_rows)
