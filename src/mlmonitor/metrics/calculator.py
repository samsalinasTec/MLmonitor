"""
MetricsCalculator — Orquestador: lee FACTs → calcula métricas → escribe FACT_METRICS_HISTORY.

Maneja dos ejes temporales por target:
- current_week: PSI/drift — usa distribuciones de la semana actual
- score_week = current_week - target.lag_semanas: Gini/KS/ordering violations

El lag es por variable target; se lee desde META_VARIABLES.lag_semanas.
"""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from mlmonitor.data.model_config import ModelConfig
from mlmonitor.db.models import (
    FactMetricsHistory,
    MetaMetricThresholds,
    MetaModelRegistry,
    MetaVariables,
)
from mlmonitor.metrics.decile_metrics import (
    DECILE_MIN_OBS,
    DECILE_WINDOW_WEEKS,
    N_DECILES,
    check_decile_ordering_violations,
    get_decile_data_for_segment,
    persist_deciles_history,
)
from mlmonitor.metrics.performance import get_gini_ks_for_segment
from mlmonitor.metrics.psi import (
    PSI_WINDOW_WEEKS,
    get_max_psi,
    get_null_rates,
    get_psi_for_all_variables,
)

_LABEL_TO_FLAG = {"OK": 0, "WARNING": 1, "CRITICAL": 2}


class AlertEvaluator:
    """Evalúa alertas usando los umbrales de META_METRIC_THRESHOLDS."""

    def __init__(self, session: Session):
        self._thresholds_cache: dict = {}
        self._metric_map: dict[tuple[str, int | None], int] = {}  # (metric_name, model_registry_id) → id
        self._load_thresholds(session)

    def _load_thresholds(self, session: Session) -> None:
        rows = (
            session.query(MetaMetricThresholds)
            .filter(MetaMetricThresholds.valid_to.is_(None))
            .all()
        )
        for r in rows:
            key = (r.metric_name, r.model_registry_id)  # model_registry_id es None para globales
            self._thresholds_cache[key] = r
            self._metric_map[key] = r.id

    def get_threshold(self, metric_name: str, model_registry_id: int) -> MetaMetricThresholds | None:
        """Busca umbral específico del modelo; si no existe, usa global (model_registry_id=None)."""
        specific = self._thresholds_cache.get((metric_name, model_registry_id))
        if specific:
            return specific
        return self._thresholds_cache.get((metric_name, None))

    def get_metric_id(self, metric_name: str, model_registry_id: int) -> int | None:
        """Retorna el ID del threshold per-segmento; fallback al global si no existe."""
        specific = self._metric_map.get((metric_name, model_registry_id))
        if specific is not None:
            return specific
        return self._metric_map.get((metric_name, None))

    def evaluate(
        self, metric_name: str, value: float, model_registry_id: int
    ) -> tuple[int, str]:
        """
        Retorna (alert_flag, alert_label):
          0 = OK, 1 = WARNING, 2 = CRITICAL
        """
        if value is None:
            return 0, "OK"

        threshold = self.get_threshold(metric_name, model_registry_id)
        if threshold is None:
            return 0, "OK"

        direction = threshold.direction or "higher_worse"
        warn = threshold.warning_threshold
        crit = threshold.critical_threshold

        if direction == "higher_worse":
            if crit is not None and value >= crit:
                return 2, "CRITICAL"
            if warn is not None and value >= warn:
                return 1, "WARNING"
        elif direction == "lower_worse":
            if crit is not None and value <= crit:
                return 2, "CRITICAL"
            if warn is not None and value <= warn:
                return 1, "WARNING"

        return 0, "OK"


class MetricsCalculator:
    """
    Calcula todas las métricas y las escribe en FACT_METRICS_HISTORY.

    `config` es opcional para preservar compatibilidad con tests legados
    que instancian el calculator sin ModelConfig. Cuando no se provee,
    las ventanas (PSI/decile) y umbrales (decile_min_obs, n_deciles) caen
    a los defaults de módulo. En el pipeline real el orchestrator carga
    siempre ModelConfig.for_model(model_id) y lo inyecta aquí.
    """

    def __init__(self, session: Session, config: ModelConfig | None = None):
        self.session = session
        self.config = config
        self.evaluator = AlertEvaluator(session)

        if config is not None:
            self.psi_window_weeks = config.psi_window_weeks
            self.decile_window_weeks = config.decile_window_weeks
            self.decile_min_obs = config.decile_min_obs
            self.n_deciles = config.n_deciles
        else:
            self.psi_window_weeks = PSI_WINDOW_WEEKS
            self.decile_window_weeks = DECILE_WINDOW_WEEKS
            self.decile_min_obs = DECILE_MIN_OBS
            self.n_deciles = N_DECILES

    def run_for_model(
        self, model_id: str, current_week: date
    ) -> list[FactMetricsHistory]:
        """
        Calcula todas las métricas para todos los segmentos del modelo
        y las escribe en FACT_METRICS_HISTORY.
        Retorna las filas insertadas.
        """
        segments = (
            self.session.query(MetaModelRegistry)
            .filter(
                MetaModelRegistry.model_id == model_id,
                MetaModelRegistry.valid_to.is_(None),
            )
            .all()
        )

        all_rows: list[FactMetricsHistory] = []

        for seg in segments:
            model_registry_id = seg.id

            # Cargar todas las variables del segmento
            var_rows = (
                self.session.query(MetaVariables)
                .filter(
                    MetaVariables.model_registry_id == model_registry_id,
                    MetaVariables.valid_to.is_(None),
                )
                .all()
            )

            # Separar inputs/outputs (para PSI/null_rate) de targets (para Gini/KS/violations)
            variable_map = {v.id: v.variable_name for v in var_rows if v.variable_rol != "target"}
            target_vars = [v for v in var_rows if v.variable_rol == "target"]

            # Resolver lag del target primario para anclar la cohorte de deciles.
            # Espejo de la lógica de report/builder.py:_build_decile_charts:
            # 1) si MetaModelRegistry.primary_target_variable está activo en este
            #    segmento, usar su lag; 2) si no, usar el lag del primer target
            #    (los targets se ordenan por declaración en META_VARIABLES).
            primary_name = seg.primary_target_variable
            primary = next(
                (t for t in target_vars if t.variable_name == primary_name),
                None,
            )
            if primary is not None:
                primary_target_lag = primary.lag_semanas or 0
            elif target_vars:
                primary_target_lag = target_vars[0].lag_semanas or 0
            else:
                primary_target_lag = 0

            rows = self._calculate_segment_metrics(
                model_registry_id=model_registry_id,
                variable_map=variable_map,
                target_vars=target_vars,
                current_week=current_week,
                score_max=seg.score_max or 1000,
                primary_target_lag=primary_target_lag,
            )
            all_rows.extend(rows)

        # Insertar en DB (ignorar conflictos por duplicado)
        for row in all_rows:
            vid_filter = (
                FactMetricsHistory.variable_id.is_(None)
                if row.variable_id is None
                else FactMetricsHistory.variable_id == row.variable_id
            )
            existing = (
                self.session.query(FactMetricsHistory)
                .filter(
                    FactMetricsHistory.model_registry_id == row.model_registry_id,
                    FactMetricsHistory.calculation_week == row.calculation_week,
                    FactMetricsHistory.metric_id == row.metric_id,
                    vid_filter,
                )
                .first()
            )
            if existing is None:
                self.session.add(row)

        self.session.flush()
        return all_rows

    def _calculate_segment_metrics(
        self,
        model_registry_id: int,
        variable_map: dict[int, str],
        target_vars: list[MetaVariables],
        current_week: date,
        score_max: int = 1000,
        primary_target_lag: int = 0,
    ) -> list[FactMetricsHistory]:
        rows = []

        # Invertir para lookup var_name → var_id
        name_to_id = {v: k for k, v in variable_map.items()}

        # Deciles per-target: cálculo único, persistencia en FACT_DECILES_HISTORY.
        # Se reutiliza el dict en memoria para detectar violaciones de orden
        # (sobre event_rate decil-a-decil) y el builder del PDF lo lee de DB
        # para renderizar las gráficas. La fuente única evita divergencia entre
        # la métrica reportada y la gráfica que la motiva.
        decile_data = get_decile_data_for_segment(
            self.session,
            model_registry_id,
            current_week,
            primary_target_lag,
            target_vars,
            min_obs=self.decile_min_obs,
            n_deciles=self.n_deciles,
            window_weeks=self.decile_window_weeks,
        )
        persist_deciles_history(
            self.session, model_registry_id, current_week, decile_data,
        )

        # --- PSI por variable (solo inputs/outputs) ---
        psi_by_var = get_psi_for_all_variables(
            self.session, model_registry_id, variable_map, current_week,
            window_weeks=self.psi_window_weeks,
        )
        for vname, psi_val in psi_by_var.items():
            _, label = self.evaluator.evaluate("psi", psi_val, model_registry_id)
            metric_id = self.evaluator.get_metric_id("psi", model_registry_id)
            var_id = name_to_id.get(vname)
            if metric_id is not None:
                rows.append(FactMetricsHistory(
                    model_registry_id=model_registry_id,
                    variable_id=var_id,
                    calculation_week=current_week,
                    metric_id=metric_id,
                    metric_value=round(psi_val, 4),
                    alert_label=label,
                    details={"variable": vname},
                    calculated_from="FACT_DISTRIBUTIONS",
                ))

        # PSI máximo del segmento
        max_psi, max_var = get_max_psi(psi_by_var)
        _, label = self.evaluator.evaluate("psi", max_psi, model_registry_id)
        metric_id = self.evaluator.get_metric_id("psi", model_registry_id)
        if metric_id is not None:
            rows.append(FactMetricsHistory(
                model_registry_id=model_registry_id,
                variable_id=None,
                calculation_week=current_week,
                metric_id=metric_id,
                metric_value=round(max_psi, 4),
                alert_label=label,
                details={"max_variable": max_var, "is_max_psi": True},
                calculated_from="FACT_DISTRIBUTIONS",
            ))

        # --- Tasas de nulos (solo inputs) ---
        null_rates = get_null_rates(
            self.session, model_registry_id, variable_map, current_week,
            window_weeks=self.psi_window_weeks,
        )
        null_metric_id = self.evaluator.get_metric_id("null_rate", model_registry_id)
        for vname, null_rate in null_rates.items():
            _, label = self.evaluator.evaluate("null_rate", null_rate, model_registry_id)
            var_id = name_to_id.get(vname)
            if null_metric_id is not None:
                rows.append(FactMetricsHistory(
                    model_registry_id=model_registry_id,
                    variable_id=var_id,
                    calculation_week=current_week,
                    metric_id=null_metric_id,
                    metric_value=round(null_rate, 4),
                    alert_label=label,
                    details={"variable": vname, "metric_subtype": "null_rate"},
                    calculated_from="FACT_DISTRIBUTIONS",
                ))

        # --- Gini, KS y violaciones de ordering — una entrada por variable target ---
        for target in target_vars:
            tname = target.variable_name
            if target.lag_semanas is None:
                raise ValueError(
                    f"Target '{tname}' (registry_id={model_registry_id}) has lag_semanas=NULL. "
                    "Every target must declare its lag explicitly in META_VARIABLES."
                )
            lag = target.lag_semanas
            origination_week = current_week - timedelta(weeks=lag)
            ascending = target.ascending_order if target.ascending_order is not None else False

            perf_metrics = get_gini_ks_for_segment(
                self.session, model_registry_id, origination_week,
                metric_type=tname,
                lag_semanas=lag,
                score_max=score_max,
            )

            if perf_metrics.get("gini") is not None:
                mname = f"gini_{tname}"
                _, label = self.evaluator.evaluate(mname, perf_metrics["gini"], model_registry_id)
                metric_id = self.evaluator.get_metric_id(mname, model_registry_id)
                if metric_id is not None:
                    rows.append(FactMetricsHistory(
                        model_registry_id=model_registry_id,
                        variable_id=None,
                        calculation_week=current_week,
                        metric_id=metric_id,
                        metric_value=perf_metrics["gini"],
                        alert_label=label,
                        details={"origination_week": origination_week.isoformat(), "target": tname},
                        calculated_from="FACT_PERFORMANCE_INDIVIDUAL",
                    ))

            if perf_metrics.get("ks") is not None:
                mname = f"ks_{tname}"
                _, label = self.evaluator.evaluate(mname, perf_metrics["ks"], model_registry_id)
                metric_id = self.evaluator.get_metric_id(mname, model_registry_id)
                if metric_id is not None:
                    rows.append(FactMetricsHistory(
                        model_registry_id=model_registry_id,
                        variable_id=None,
                        calculation_week=current_week,
                        metric_id=metric_id,
                        metric_value=perf_metrics["ks"],
                        alert_label=label,
                        details={"origination_week": origination_week.isoformat(), "target": tname},
                        calculated_from="FACT_PERFORMANCE_INDIVIDUAL",
                    ))

            # Violaciones de orden sobre deciles (no sobre bins fijos de score).
            # decile_data lo computamos arriba; aquí solo extraemos la tabla del
            # target. Si no hay deciles (cohorte sin datos o n<min_obs), 0 viol.
            pt_entry = decile_data["per_target"].get(tname, {})
            decile_table = pt_entry.get("decile_table")
            if decile_table is not None and not decile_table.empty:
                violations = check_decile_ordering_violations(
                    decile_table, ascending=ascending,
                )
                window_start = pt_entry.get("cohort_window_start")
                window_end = pt_entry.get("cohort_window_end")
            else:
                violations = {"violations": 0, "violation_pairs": []}
                window_start = None
                window_end = None
            n_v = violations.get("violations", 0)
            mname = f"ordering_violations_{tname}"
            _, label = self.evaluator.evaluate(mname, n_v, model_registry_id)
            metric_id = self.evaluator.get_metric_id(mname, model_registry_id)
            if metric_id is not None:
                rows.append(FactMetricsHistory(
                    model_registry_id=model_registry_id,
                    variable_id=None,
                    calculation_week=current_week,
                    metric_id=metric_id,
                    metric_value=float(n_v),
                    alert_label=label,
                    details={
                        "target": tname,
                        "cohort_window_start": window_start.isoformat() if window_start else None,
                        "cohort_window_end": window_end.isoformat() if window_end else None,
                        "violation_pairs": violations.get("violation_pairs", []),
                    },
                    calculated_from="FACT_DECILES_HISTORY",
                ))

        return rows

    def get_current_metrics_for_segment(
        self,
        model_registry_id: int,
        calculation_week: date,
        metric_name_map: dict[int, str],
    ) -> dict[str, dict]:
        """
        Lee las métricas calculadas de FACT_METRICS_HISTORY para un segmento.

        Args:
            model_registry_id: ID surrogado del segmento
            calculation_week: semana de cálculo
            metric_name_map: {metric_id: metric_name} para resolver nombres

        Returns:
            {metric_name: {value, alert_flag, alert_label, details}}
        """
        rows = (
            self.session.query(FactMetricsHistory)
            .filter(
                FactMetricsHistory.model_registry_id == model_registry_id,
                FactMetricsHistory.calculation_week == calculation_week,
            )
            .all()
        )
        result = {}
        for r in rows:
            mname = metric_name_map.get(r.metric_id, f"metric_{r.metric_id}")
            key = mname
            if r.variable_id is not None and r.details:
                sub = r.details.get("variable") or r.details.get("metric_subtype")
                if sub and sub != "null_rate":
                    key = f"{mname}_{sub}"
                elif r.details.get("metric_subtype") == "null_rate":
                    key = f"null_rate_{r.details.get('variable', '')}"
            result[key] = {
                "value": r.metric_value,
                "alert_flag": _LABEL_TO_FLAG.get(r.alert_label, 0),
                "alert_label": r.alert_label,
                "details": r.details,
            }
        return result
