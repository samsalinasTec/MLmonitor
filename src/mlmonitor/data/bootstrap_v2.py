"""
ModelBootstrapV2 — Variante experimental del bootstrap.

Diferencia vs `bootstrap.ModelBootstrap`: el baseline para
`META_BASELINE_DISTRIBUTIONS` se calcula desde las **primeras N semanas ISO
del año indicado** dentro de `variables_serc_*.csv` (formato LONG), en lugar
de leer `base_train_test_bb.csv` (formato WIDE).

Resto del bootstrap (META_MODEL_REGISTRY, META_VARIABLES, META_METRIC_THRESHOLDS)
es idéntico — heredado sin cambios.

Es experimental: NO sustituye a `ModelBootstrap`. Co-existe para comparar
resultados de PSI con un baseline derivado de la misma fuente que las
distribuciones semanales en lugar de la base de entrenamiento original.
"""

import logging
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from mlmonitor.data.bootstrap import (
    MISSING_SENTINEL,
    NUM_BINS_NUMERIC,
    SCORE_BIN_LABELS,
    SCORE_BINS,
    ModelBootstrap,
)
from mlmonitor.data.variable_mapping import CANONICAL_VARIABLES, serc_to_canonical
from mlmonitor.db.models import MetaBaselineDistributions, MetaVariables

logger = logging.getLogger(__name__)


_SERC_USECOLS = [
    "fiidscoreds",
    "fiidsegmento",
    "fnpuntaje",
    "fcnombre_variable",
    "fcvalor_variable",
    "fdregistro_solicitud",
]


class ModelBootstrapV2(ModelBootstrap):
    """Bootstrap experimental: baseline desde variables_serc (primeras N semanas del año)."""

    def __init__(
        self,
        session: Session,
        raw_dir: str | Path = "data/inputs/raw_tables",
        baseline_filename: str | None = None,
        baseline_year: int = 2026,
        baseline_n_weeks: int = 4,
        variables_serc_filename: str | None = None,
    ):
        super().__init__(session, raw_dir=raw_dir, baseline_filename=baseline_filename)
        self.baseline_year = baseline_year
        self.baseline_n_weeks = baseline_n_weeks
        self._variables_serc_filename = variables_serc_filename

    # ------------------------------------------------------------------
    # Resolución de archivo y carga
    # ------------------------------------------------------------------

    def _resolve_variables_serc_path(self) -> Path:
        if self._variables_serc_filename:
            return self.raw_dir / self._variables_serc_filename
        candidates = sorted(self.raw_dir.glob("variables_serc_*.csv"))
        if not candidates:
            raise FileNotFoundError(
                f"No se encontró variables_serc_*.csv en {self.raw_dir}"
            )
        # último lexicográfico = más reciente (convención <prefix>_<YYYYMMDD>_*)
        return candidates[-1]

    def _baseline_weeks(self) -> list[date]:
        """Lista de Mondays ISO para las primeras N semanas del año configurado."""
        return [
            date.fromisocalendar(self.baseline_year, w, 1)
            for w in range(1, self.baseline_n_weeks + 1)
        ]

    def _load_serc_baseline_window(self) -> pd.DataFrame:
        """Lee variables_serc y filtra a las primeras N semanas ISO del año."""
        path = self._resolve_variables_serc_path()
        logger.info("Loading variables_serc from %s", path)
        df = pd.read_csv(path, usecols=_SERC_USECOLS, low_memory=False)
        logger.info("Raw variables_serc shape: %s", df.shape)

        ts = pd.to_datetime(df["fdregistro_solicitud"], unit="ms", errors="coerce")
        iso = ts.dt.isocalendar()  # year, week, day
        df["_iso_year"] = iso["year"].astype("Int64")
        df["_iso_week"] = iso["week"].astype("Int64")

        weeks_target = list(range(1, self.baseline_n_weeks + 1))
        mask = (df["_iso_year"] == self.baseline_year) & (df["_iso_week"].isin(weeks_target))
        out = df.loc[mask].copy()

        # Derivar Monday ISO para alinearse con resto del código (no se persiste,
        # solo informa logs/filtrado fino si fuera necesario).
        out["_origination_week"] = [
            date.fromisocalendar(int(y), int(w), 1)
            for y, w in zip(out["_iso_year"], out["_iso_week"])
        ]

        n_creditos = out["fiidscoreds"].nunique()
        logger.info(
            "Baseline window (year=%d, weeks=1..%d): %d rows, %d unique creditos",
            self.baseline_year, self.baseline_n_weeks, len(out), n_creditos,
        )
        return out

    # ------------------------------------------------------------------
    # Override: baseline distributions desde LONG en vez de WIDE
    # ------------------------------------------------------------------

    def _populate_baseline_distributions(self) -> int:
        baseline_df = self._load_serc_baseline_window()
        if baseline_df.empty:
            logger.warning(
                "Baseline ventana vacía para year=%d weeks=1..%d — sin filas insertadas",
                self.baseline_year, self.baseline_n_weeks,
            )
            return 0

        # Map SERC → canonical y descarta filas no canónicas
        baseline_df["_canonical"] = baseline_df["fcnombre_variable"].apply(serc_to_canonical)
        canonical_df = baseline_df.dropna(subset=["_canonical"]).copy()

        # Preserva valor original (categóricas) y convierte numérico
        canonical_df["_fcvalor_original"] = canonical_df["fcvalor_variable"]
        canonical_df["fcvalor_variable"] = pd.to_numeric(
            canonical_df["fcvalor_variable"], errors="coerce"
        )

        total = 0
        total += self._baseline_variable_distributions_serc(canonical_df)
        total += self._baseline_score_distributions_serc(baseline_df)
        return total

    def _baseline_variable_distributions_serc(self, df: pd.DataFrame) -> int:
        """Variables input: numéricas → qcut + cuts; categóricas → categorías directas."""
        all_rows: list[MetaBaselineDistributions] = []

        for seg_id in range(1, 12):
            submodel_id = f"s{seg_id}"
            reg_id = self._registry_map.get(submodel_id)
            if reg_id is None:
                continue

            seg_df = df[df["fiidsegmento"] == seg_id]
            if seg_df.empty:
                logger.warning("V2: no baseline data for segment %d in window", seg_id)
                continue

            canonical_vars = CANONICAL_VARIABLES.get(seg_id, [])

            for vname in canonical_vars:
                var_id = self._variable_map.get((submodel_id, vname))
                if var_id is None:
                    continue

                var_rows = seg_df[seg_df["_canonical"] == vname]
                if var_rows.empty:
                    logger.warning(
                        "V2: variable '%s' no encontrada en window (segmento %d)",
                        vname, seg_id,
                    )
                    continue

                is_categorical = vname == "fisexo"
                if is_categorical:
                    series = var_rows["_fcvalor_original"]
                    all_rows.extend(self._bin_categorical_baseline(series, reg_id, var_id))
                else:
                    series = var_rows["fcvalor_variable"]
                    all_rows.extend(self._bin_numeric_baseline_v2(series, reg_id, var_id))

        if all_rows:
            self.session.add_all(all_rows)
            self.session.flush()
        logger.info("V2 META_BASELINE_DISTRIBUTIONS (variables): %d rows", len(all_rows))
        return len(all_rows)

    def _bin_numeric_baseline_v2(
        self, values: pd.Series, reg_id: int, var_id: int,
    ) -> list[MetaBaselineDistributions]:
        """qcut sobre la ventana baseline; persiste cuts en MetaVariables.binning_rules."""
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

    def _baseline_score_distributions_serc(self, baseline_df: pd.DataFrame) -> int:
        """Score: dedup por fiidscoreds dentro de la ventana, bins fijos por segmento."""
        # Dedup: un score por crédito
        score_df = (
            baseline_df.groupby("fiidscoreds")
            .agg(
                fiidsegmento=("fiidsegmento", "first"),
                fnpuntaje=("fnpuntaje", "first"),
            )
            .reset_index()
            .dropna(subset=["fnpuntaje"])
        )

        all_rows: list[MetaBaselineDistributions] = []
        last_idx = len(SCORE_BINS) - 1

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
        logger.info("V2 META_BASELINE_DISTRIBUTIONS (scores): %d rows", len(all_rows))
        return len(all_rows)
