"""
IncrementalETL — Carga semanal incremental de tablas FACT.

Asume que las tablas META ya estan pobladas (por bootstrap.py).
Lee toda la configuracion (segments, variables, bin_rules, targets+lags) desde la DB.

Dos flujos independientes por semana W:
  Flow A (distribuciones): nuevos creditos scoreados en semana W → FACT_DISTRIBUTIONS
  Flow B (performance): cohortes que maduran en semana W → FACT_PERFORMANCE_BINNED + _INDIVIDUAL

Uso:
    etl = IncrementalETL(session)
    result = etl.run(execution_week, variables_df, weekly_df)
"""

import logging
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from mlmonitor.data.model_config import ModelConfig
from mlmonitor.db.models import (
    FactDistributions,
    FactPerformanceBinned,
    FactPerformanceIndividual,
    MetaModelRegistry,
    MetaVariables,
)

logger = logging.getLogger(__name__)


def _date_to_iso_week(d: date) -> int:
    """Convierte date a semana ISO como entero (ej: 202541)."""
    y, w, _ = d.isocalendar()
    return y * 100 + w


class IncrementalETL:
    """Carga incremental semanal de FACT tables."""

    def __init__(self, session: Session, config: ModelConfig):
        self.session = session
        self.config = config
        self.model_id = config.model_id

        # Config leida de META tables (poblada por _load_config_from_db)
        self._segments: dict[str, int] = {}        # submodel_id -> registry_id
        self._input_vars: dict[int, list[dict]] = {}  # registry_id -> [{id, name, type, binning_rules, woe_categories}]
        self._score_vars: dict[int, int] = {}       # registry_id -> score variable_id
        self._score_bin_cuts: dict[int, list] = {}  # registry_id -> bin cuts from META
        self._target_vars: dict[int, list[dict]] = {}  # registry_id -> [{name, lag_semanas, ascending_order}]

        self._load_config_from_db()

    def _load_config_from_db(self) -> None:
        """Lee toda la configuracion necesaria desde tablas META."""
        regs = (
            self.session.query(MetaModelRegistry)
            .filter(
                MetaModelRegistry.model_id == self.model_id,
                MetaModelRegistry.valid_to.is_(None),
            )
            .all()
        )
        if not regs:
            raise ValueError(f"No active model registries found for model_id={self.model_id}")

        for reg in regs:
            self._segments[reg.submodel_id] = reg.id

            variables = (
                self.session.query(MetaVariables)
                .filter(
                    MetaVariables.model_registry_id == reg.id,
                    MetaVariables.valid_to.is_(None),
                )
                .all()
            )

            inputs = []
            targets = []
            for v in variables:
                if v.variable_rol == "input":
                    inputs.append({
                        "id": v.id,
                        "name": v.variable_name,
                        "type": v.variable_type,
                        "binning_rules": v.binning_rules,
                        "woe_categories": v.woe_categories,
                    })
                elif v.variable_rol == "output" and v.variable_name == "score":
                    self._score_vars[reg.id] = v.id
                    if v.binning_rules and v.binning_rules.get("type") == "fixed_cuts":
                        self._score_bin_cuts[reg.id] = v.binning_rules["cuts"]
                elif v.variable_rol == "target":
                    if v.lag_semanas is None:
                        raise ValueError(
                            f"Target '{v.variable_name}' (segment registry_id={reg.id}) has lag_semanas=NULL. "
                            "Every target must declare its lag explicitly in META_VARIABLES."
                        )
                    targets.append({
                        "name": v.variable_name,
                        "lag_semanas": v.lag_semanas,
                        "ascending_order": v.ascending_order if v.ascending_order is not None else False,
                    })

            self._input_vars[reg.id] = inputs
            self._target_vars[reg.id] = targets

        logger.info(
            "Config loaded: %d segments, %d total input vars, %d targets/seg",
            len(self._segments),
            sum(len(v) for v in self._input_vars.values()),
            len(next(iter(self._target_vars.values()), [])),
        )

    @staticmethod
    def detect_execution_week(weekly_df: pd.DataFrame) -> date:
        """Deriva W desde MAX(semana_observacion) del CSV, normalizada a date.

        semana_observacion es la semana en que se evaluaron los outcomes — es el
        techo natural para W. Retorna el Monday de esa ISO week.
        """
        max_obs = int(weekly_df["semana_observacion"].max())
        year, week = divmod(max_obs, 100)
        return date.fromisocalendar(year, week, 1)

    def run(
        self,
        execution_week: date | None = None,
        variables_df: pd.DataFrame | None = None,
        weekly_df: pd.DataFrame | None = None,
    ) -> dict:
        """Ejecuta ambos flujos para la semana W.

        Args:
            execution_week: semana de ejecucion (W). Si None, se auto-detecta
                desde MAX(semana_observacion) en weekly_df.
            variables_df: DataFrame de variables_serc (ya filtrado a semana W o completo).
            weekly_df: DataFrame de muestra_weekly (completo; se filtrara por semana_num).

        Returns:
            Dict con conteos de filas insertadas por tabla.
        """
        # Auto-detect execution week from data if not provided
        if execution_week is None:
            if weekly_df is not None and not weekly_df.empty:
                execution_week = self.detect_execution_week(weekly_df)
                logger.info("Auto-detected execution_week=%s from semana_observacion", execution_week)
            elif variables_df is not None and not variables_df.empty:
                raise ValueError("execution_week is required when only variables_df is provided (no weekly_df to auto-detect from)")
            else:
                raise ValueError("execution_week is required when no DataFrames are provided")
        else:
            # Validate against semana_observacion if available
            if weekly_df is not None and not weekly_df.empty:
                w_obs = self.detect_execution_week(weekly_df)
                if execution_week > w_obs:
                    logger.warning(
                        "execution_week=%s > semana_observacion=%s — outcomes may not be fully observed",
                        execution_week, w_obs,
                    )

        # Detect data lag vs calendar
        calendar_monday = date.today() - timedelta(days=date.today().weekday())
        data_lag_weeks = (calendar_monday - execution_week).days // 7
        if data_lag_weeks > 0:
            logger.warning(
                "Data lag detected: execution_week=%s, current calendar week=%s (%d weeks behind)",
                execution_week, calendar_monday, data_lag_weeks,
            )

        result = {}

        if variables_df is not None and not variables_df.empty:
            result.update(self._flow_a_distributions(execution_week, variables_df))
        else:
            logger.info("Flow A skipped: no variables_df provided")
            result["distributions_rows"] = 0

        if weekly_df is not None and not weekly_df.empty:
            result.update(self._flow_b_performance(execution_week, weekly_df))
        else:
            logger.info("Flow B skipped: no weekly_df provided")
            result["performance_binned_rows"] = 0
            result["performance_individual_rows"] = 0

        return result

    # ------------------------------------------------------------------
    # Flow A: Distribuciones de variables (PSI)
    # ------------------------------------------------------------------

    def _flow_a_distributions(self, execution_week: date, variables_df: pd.DataFrame) -> dict:
        """Flow A: nuevos creditos scoreados en semana W → FACT_DISTRIBUTIONS."""

        # Idempotencia: check si ya existen datos para esta semana
        existing = (
            self.session.query(FactDistributions.id)
            .filter(
                FactDistributions.origination_week == execution_week,
            )
            .first()
        )
        if existing:
            logger.info("Flow A: data already exists for week %s, skipping", execution_week)
            return {"distributions_rows": 0}

        # Preparar DataFrame
        df = variables_df.copy()

        # Si no tiene _origination_week, derivarla de fdregistro_solicitud
        if "_origination_week" not in df.columns:
            df["_ts"] = pd.to_datetime(df["fdregistro_solicitud"], unit="ms", errors="coerce")
            df["_origination_week"] = df["_ts"].apply(
                lambda t: date.fromisocalendar(t.isocalendar()[0], t.isocalendar()[1], 1) if pd.notna(t) else None
            )

        # Filtrar a la semana de ejecucion
        df = df[df["_origination_week"] == execution_week].copy()
        if df.empty:
            logger.info("Flow A: no SERC data for week %s", execution_week)
            return {"distributions_rows": 0}

        df["_canonical"] = df["fcnombre_variable"].apply(self.config.serc_to_canonical)
        df = df.dropna(subset=["_canonical"])
        # Preserve original values for categoricals; convert numerics separately
        df["_fcvalor_original"] = df["fcvalor_variable"]
        df["fcvalor_variable"] = pd.to_numeric(df["fcvalor_variable"], errors="coerce")

        all_rows: list[FactDistributions] = []

        # Variables de input
        for (seg_id, canonical), grp in df.groupby(["fiidsegmento", "_canonical"]):
            submodel_id = f"s{seg_id}"
            reg_id = self._segments.get(submodel_id)
            if reg_id is None:
                continue

            var_config = next(
                (v for v in self._input_vars.get(reg_id, []) if v["name"] == canonical),
                None,
            )
            if var_config is None:
                continue

            if var_config["type"] == "categorical":
                # Use original string values for categoricals
                cat_grp = grp.copy()
                cat_grp["fcvalor_variable"] = cat_grp["_fcvalor_original"]
                all_rows.extend(self._bin_categorical(
                    cat_grp, reg_id, var_config, execution_week
                ))
            else:
                all_rows.extend(self._bin_numeric(
                    grp, reg_id, var_config, execution_week
                ))

        # Score distribution
        all_rows.extend(self._bin_score(df, execution_week))

        if all_rows:
            self.session.add_all(all_rows)
            self.session.flush()

        logger.info("Flow A: %d distribution rows for week %s", len(all_rows), execution_week)
        return {"distributions_rows": len(all_rows)}

    def _bin_numeric(
        self,
        grp: pd.DataFrame,
        reg_id: int,
        var_config: dict,
        week: date,
    ) -> list[FactDistributions]:
        """Bin numeric variable using fixed cuts from META_VARIABLES.binning_rules."""
        binning_rules = var_config.get("binning_rules")
        if not binning_rules or binning_rules.get("type") != "fixed_cuts":
            logger.warning(
                "No fixed_cuts binning_rules for variable %s (reg_id=%d), skipping",
                var_config["name"], reg_id,
            )
            return []

        bin_edges = np.array(binning_rules["cuts"])
        n_bins = len(bin_edges) - 1
        var_id = var_config["id"]

        vals = grp["fcvalor_variable"]
        total_records = len(vals)
        sentinel = self.config.missing_sentinel
        null_count = int(vals.isna().sum() + (vals == sentinel).sum())
        clean = vals[(vals.notna()) & (vals != sentinel)]

        if clean.empty:
            return []

        bin_indices = np.digitize(clean.values, bin_edges[1:-1])

        rows = []
        for bin_idx in range(n_bins):
            label = f"bin_{bin_idx + 1}"
            count = int((bin_indices == bin_idx).sum())
            pct = count / len(clean) if len(clean) > 0 else 0.0

            rows.append(FactDistributions(
                model_registry_id=reg_id,
                variable_id=var_id,
                origination_week=week,
                bin_label=label,
                bin_count=count,
                bin_percentage=round(pct, 6),
                null_count=null_count if bin_idx == 0 else 0,
                sum_value=None,
                total_records=total_records,
            ))
        return rows

    def _bin_categorical(
        self,
        grp: pd.DataFrame,
        reg_id: int,
        var_config: dict,
        week: date,
    ) -> list[FactDistributions]:
        """Bin categorical variable using categories from META_VARIABLES.woe_categories."""
        ref_categories = var_config.get("woe_categories")
        if not ref_categories:
            logger.warning(
                "No woe_categories for variable %s (reg_id=%d), skipping",
                var_config["name"], reg_id,
            )
            return []

        var_id = var_config["id"]
        vals = grp["fcvalor_variable"].astype(str)
        total_records = len(vals)
        null_count = int(grp["fcvalor_variable"].isna().sum())

        rows = []
        for cat_val in ref_categories:
            count = int((vals == cat_val).sum())
            pct = count / total_records if total_records > 0 else 0.0
            rows.append(FactDistributions(
                model_registry_id=reg_id,
                variable_id=var_id,
                origination_week=week,
                bin_label=str(cat_val),
                bin_count=count,
                bin_percentage=round(pct, 6),
                null_count=null_count,
                sum_value=None,
                total_records=total_records,
            ))

        # New categories not in reference → __other__
        other_vals = vals[~vals.isin(ref_categories)]
        if len(other_vals) > 0:
            other_count = len(other_vals)
            rows.append(FactDistributions(
                model_registry_id=reg_id,
                variable_id=var_id,
                origination_week=week,
                bin_label="__other__",
                bin_count=other_count,
                bin_percentage=round(other_count / total_records, 6),
                null_count=0,
                sum_value=None,
                total_records=total_records,
            ))
        return rows

    def _bin_score(self, df: pd.DataFrame, week: date) -> list[FactDistributions]:
        """Bin score distribution per segment using cuts from META_VARIABLES."""
        # Dedup: un score por credito
        score_df = (
            df.groupby("fiidscoreds")
            .agg(
                fiidsegmento=("fiidsegmento", "first"),
                fnpuntaje=("fnpuntaje", "first"),
            )
            .reset_index()
        )
        score_df = score_df.dropna(subset=["fnpuntaje"])

        all_rows = []
        for seg_id, grp in score_df.groupby("fiidsegmento"):
            submodel_id = f"s{seg_id}"
            reg_id = self._segments.get(submodel_id)
            if reg_id is None:
                continue

            score_var_id = self._score_vars.get(reg_id)
            if score_var_id is None:
                continue

            bin_cuts = self._score_bin_cuts.get(reg_id)
            if not bin_cuts or len(bin_cuts) < 2:
                continue

            total_records = len(grp)
            scores = grp["fnpuntaje"]

            for i in range(len(bin_cuts) - 1):
                lo, hi = bin_cuts[i], bin_cuts[i + 1]
                label = f"{lo}-{hi}"
                if i == len(bin_cuts) - 2:
                    in_bin = scores[(scores >= lo) & (scores <= hi)]
                else:
                    in_bin = scores[(scores >= lo) & (scores < hi)]
                count = len(in_bin)
                pct = count / total_records if total_records > 0 else 0.0

                all_rows.append(FactDistributions(
                    model_registry_id=reg_id,
                    variable_id=score_var_id,
                    origination_week=week,
                    bin_label=label,
                    bin_count=count,
                    bin_percentage=round(pct, 6),
                    null_count=0,
                    sum_value=float(in_bin.sum()) if count > 0 else 0.0,
                    total_records=total_records,
                ))

        return all_rows

    # ------------------------------------------------------------------
    # Flow B: Performance (cohortes maduras)
    # ------------------------------------------------------------------

    def _flow_b_performance(self, execution_week: date, weekly_df: pd.DataFrame) -> dict:
        """Flow B: cohortes que maduran en semana W → FACT_PERFORMANCE_BINNED + _INDIVIDUAL.

        Para cada target con lag L:
        - Filtra semana_num = iso_week(W - L) → creditos surtidos hace L semanas
        - La madurez se garantiza por el filtro: no se calcula _semanas_vida
        """
        df = weekly_df.copy()
        df = df[df["flg_surtida"] == 1].copy()
        if df.empty:
            logger.warning("Flow B: no disbursed records")
            return {"performance_binned_rows": 0, "performance_individual_rows": 0}


        all_binned: list[FactPerformanceBinned] = []
        all_individual: list[FactPerformanceIndividual] = []
        seen_individual: set[tuple] = set()

        # Procesar por target (cada uno tiene su lag y por tanto su cohorte)
        # Usamos los targets del primer segmento (son iguales para todos)
        first_reg_id = next(iter(self._target_vars))
        targets = self._target_vars[first_reg_id]

        for target in targets:
            tname = target["name"]
            lag = target["lag_semanas"]

            if tname not in df.columns:
                logger.warning("Flow B: target column '%s' not in data, skipping", tname)
                continue

            # Semana de surtimiento de la cohorte que madura en W
            disbursement_date = execution_week - timedelta(weeks=lag)
            disbursement_iso = _date_to_iso_week(disbursement_date)

            # Filtrar creditos surtidos en esa semana
            cohort = df[df["semana_num"].apply(int) == disbursement_iso].copy()
            if cohort.empty:
                logger.info(
                    "Flow B: no records for target=%s, disbursement_week=%d (W-%d)",
                    tname, disbursement_iso, lag,
                )
                continue

            # Filtrar target no-null
            cohort = cohort[cohort[tname].notna()].copy()
            if cohort.empty:
                continue

            # Aritmética alineada con calculator.py: origination_week = current_week - timedelta(weeks=lag)
            disbursement_week_date = disbursement_date

            # Procesar por segmento
            for seg_id, seg_grp in cohort.groupby("fiidsegmento"):
                submodel_id = f"s{seg_id}"
                reg_id = self._segments.get(submodel_id)
                if reg_id is None:
                    continue

                # Idempotencia check por (segment, target, disbursement_week)
                existing = (
                    self.session.query(FactPerformanceBinned.id)
                    .filter(
                        FactPerformanceBinned.model_registry_id == reg_id,
                        FactPerformanceBinned.origination_week == disbursement_week_date,
                        FactPerformanceBinned.metric_type == tname,
                    )
                    .first()
                )
                if existing:
                    logger.info(
                        "Flow B: data exists for %s/%s/week=%s, skipping",
                        submodel_id, tname, disbursement_week_date,
                    )
                    continue

                # Score bins from META
                bin_cuts = self._score_bin_cuts.get(reg_id)
                if not bin_cuts or len(bin_cuts) < 2:
                    continue

                bin_labels = [f"{bin_cuts[i]}-{bin_cuts[i+1]}" for i in range(len(bin_cuts) - 1)]
                bin_midpoints = [(bin_cuts[i] + bin_cuts[i+1]) // 2 for i in range(len(bin_cuts) - 1)]

                cut_edges = list(bin_cuts)
                cut_edges[-1] = cut_edges[-1] + 1  # include score_max
                seg_grp["_score_bin"] = pd.cut(
                    seg_grp["fnpuntaje"],
                    bins=cut_edges,
                    labels=bin_labels,
                    right=False,
                )

                # FACT_PERFORMANCE_BINNED
                for score_bin, bin_grp in seg_grp.groupby("_score_bin", observed=True):
                    count_total = len(bin_grp)
                    bin_idx = bin_labels.index(str(score_bin)) if str(score_bin) in bin_labels else None
                    midpoint = bin_midpoints[bin_idx] if bin_idx is not None else None
                    valid = bin_grp[tname].dropna()
                    count_event_real = int(valid.sum()) if not valid.empty else 0

                    all_binned.append(FactPerformanceBinned(
                        model_registry_id=reg_id,
                        origination_week=disbursement_week_date,
                        execution_week=execution_week,
                        metric_type=tname,
                        score_bin=str(score_bin),
                        score_midpoint=int(midpoint) if midpoint is not None else None,
                        count_total=count_total,
                        count_event_real=count_event_real,
                        sum_predicted_score=float(bin_grp["fnpuntaje"].sum()),
                    ))

                # FACT_PERFORMANCE_INDIVIDUAL
                for _, row in seg_grp.iterrows():
                    credito_id = str(row["fiidscoreds"])
                    key = (credito_id, reg_id, tname)
                    if key in seen_individual:
                        continue
                    seen_individual.add(key)

                    all_individual.append(FactPerformanceIndividual(
                        credito_id=credito_id,
                        model_registry_id=reg_id,
                        origination_week=disbursement_week_date,
                        execution_week=execution_week,
                        fnpuntaje=float(row["fnpuntaje"]),
                        ventana=tname,
                        flag=int(row[tname]),
                    ))

        # Batch insert
        if all_binned:
            self.session.add_all(all_binned)
            self.session.flush()

        if all_individual:
            self.session.add_all(all_individual)
            self.session.flush()

        logger.info(
            "Flow B: %d binned rows, %d individual rows for week %s",
            len(all_binned), len(all_individual), execution_week,
        )
        return {
            "performance_binned_rows": len(all_binned),
            "performance_individual_rows": len(all_individual),
        }
