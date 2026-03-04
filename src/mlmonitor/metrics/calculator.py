"""
MetricsCalculator — Orquestador: lee FACTs → calcula métricas → escribe FACT_METRICS_HISTORY.

Maneja dos ejes temporales:
- current_week: PSI/drift — usa distribuciones de la semana actual
- performance_week = current_week - 8 semanas: Gini/KS/RollForward
"""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from mlmonitor.db.models import (
    FactMetricsHistory,
    MetaMetricThresholds,
    MetaModelRegistry,
    MetaVariables,
)
from mlmonitor.metrics.business_metrics import (
    get_business_metrics_table,
    get_payment_rate_violations,
    get_roll_forward_violations,
)
from mlmonitor.metrics.performance import get_gini_ks_for_segment
from mlmonitor.metrics.psi import get_max_psi, get_null_rates, get_psi_for_all_variables

LAG_WEEKS = 8

_LABEL_TO_FLAG = {"OK": 0, "WARNING": 1, "CRITICAL": 2}


class AlertEvaluator:
    """Evalúa alertas usando los umbrales de META_METRIC_THRESHOLDS."""

    def __init__(self, session: Session):
        self._thresholds_cache: dict = {}
        self._metric_map: dict[str, int] = {}  # metric_name → metric_id
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
            # Construir mapa de nombre → id (solo si no existe ya)
            if r.metric_name not in self._metric_map:
                self._metric_map[r.metric_name] = r.id

    def get_threshold(self, metric_name: str, model_registry_id: int) -> MetaMetricThresholds | None:
        """Busca umbral específico del modelo; si no existe, usa global (model_registry_id=None)."""
        specific = self._thresholds_cache.get((metric_name, model_registry_id))
        if specific:
            return specific
        return self._thresholds_cache.get((metric_name, None))

    def get_metric_id(self, metric_name: str) -> int | None:
        """Retorna el ID surrogado del threshold por nombre de métrica."""
        return self._metric_map.get(metric_name)

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
    """

    def __init__(self, session: Session):
        self.session = session
        self.evaluator = AlertEvaluator(session)

    def run_for_model(
        self, model_id: str, current_week: date
    ) -> list[FactMetricsHistory]:
        """
        Calcula todas las métricas para todos los segmentos del modelo
        y las escribe en FACT_METRICS_HISTORY.
        Retorna las filas insertadas.
        """
        performance_week = current_week - timedelta(weeks=LAG_WEEKS)

        # Obtener segmentos activos del modelo
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
            fleet_id = seg.fleet_id

            # Cargar variables del segmento: {var_id: var_name}
            var_rows = (
                self.session.query(MetaVariables)
                .filter(
                    MetaVariables.model_registry_id == model_registry_id,
                    MetaVariables.valid_to.is_(None),
                )
                .all()
            )
            variable_map = {v.id: v.variable_name for v in var_rows}

            rows = self._calculate_segment_metrics(
                model_registry_id=model_registry_id,
                variable_map=variable_map,
                current_week=current_week,
                performance_week=performance_week,
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
        current_week: date,
        performance_week: date,
    ) -> list[FactMetricsHistory]:
        rows = []

        # Invertir para lookup var_name → var_id
        name_to_id = {v: k for k, v in variable_map.items()}

        # --- PSI por variable ---
        psi_by_var = get_psi_for_all_variables(
            self.session, model_registry_id, variable_map, current_week
        )
        for vname, psi_val in psi_by_var.items():
            _, label = self.evaluator.evaluate("psi", psi_val, model_registry_id)
            metric_id = self.evaluator.get_metric_id("psi")
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
        metric_id = self.evaluator.get_metric_id("psi")
        if metric_id is not None:
            rows.append(FactMetricsHistory(
                model_registry_id=model_registry_id,
                variable_id=None,  # métrica de segmento, no de variable específica
                calculation_week=current_week,
                metric_id=metric_id,
                metric_value=round(max_psi, 4),
                alert_label=label,
                details={"max_variable": max_var, "is_max_psi": True},
                calculated_from="FACT_DISTRIBUTIONS",
            ))

        # --- Tasas de nulos ---
        null_rates = get_null_rates(self.session, model_registry_id, variable_map, current_week)
        null_metric_id = self.evaluator.get_metric_id("null_rate")
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

        # --- Gini y KS (con lag de 8 semanas) ---
        perf_metrics = get_gini_ks_for_segment(
            self.session, model_registry_id, performance_week
        )
        if perf_metrics.get("gini") is not None:
            _, label = self.evaluator.evaluate("gini", perf_metrics["gini"], model_registry_id)
            metric_id = self.evaluator.get_metric_id("gini")
            if metric_id is not None:
                rows.append(FactMetricsHistory(
                    model_registry_id=model_registry_id,
                    variable_id=None,
                    calculation_week=current_week,
                    metric_id=metric_id,
                    metric_value=perf_metrics["gini"],
                    alert_label=label,
                    details={"performance_week": performance_week.isoformat()},
                    calculated_from="FACT_PERFORMANCE_OUTCOMES",
                ))

        if perf_metrics.get("ks") is not None:
            _, label = self.evaluator.evaluate("ks", perf_metrics["ks"], model_registry_id)
            metric_id = self.evaluator.get_metric_id("ks")
            if metric_id is not None:
                rows.append(FactMetricsHistory(
                    model_registry_id=model_registry_id,
                    variable_id=None,
                    calculation_week=current_week,
                    metric_id=metric_id,
                    metric_value=perf_metrics["ks"],
                    alert_label=label,
                    details={"performance_week": performance_week.isoformat()},
                    calculated_from="FACT_PERFORMANCE_OUTCOMES",
                ))

        # --- Violaciones de ordering (con lag) ---
        rf_violations = get_roll_forward_violations(
            self.session, model_registry_id, performance_week
        )
        n_rf = rf_violations.get("violations", 0)
        _, label = self.evaluator.evaluate(
            "roll_forward_ordering_violations", n_rf, model_registry_id
        )
        metric_id = self.evaluator.get_metric_id("roll_forward_ordering_violations")
        if metric_id is not None:
            rows.append(FactMetricsHistory(
                model_registry_id=model_registry_id,
                variable_id=None,
                calculation_week=current_week,
                metric_id=metric_id,
                metric_value=float(n_rf),
                alert_label=label,
                details={
                    "performance_week": performance_week.isoformat(),
                    "violation_pairs": rf_violations.get("violation_pairs", []),
                },
                calculated_from="FACT_PERFORMANCE_OUTCOMES",
            ))

        pr_violations = get_payment_rate_violations(
            self.session, model_registry_id, performance_week
        )
        n_pr = pr_violations.get("violations", 0)
        _, label = self.evaluator.evaluate(
            "payment_rate_ordering_violations", n_pr, model_registry_id
        )
        metric_id = self.evaluator.get_metric_id("payment_rate_ordering_violations")
        if metric_id is not None:
            rows.append(FactMetricsHistory(
                model_registry_id=model_registry_id,
                variable_id=None,
                calculation_week=current_week,
                metric_id=metric_id,
                metric_value=float(n_pr),
                alert_label=label,
                details={
                    "performance_week": performance_week.isoformat(),
                    "violation_pairs": pr_violations.get("violation_pairs", []),
                },
                calculated_from="FACT_PERFORMANCE_OUTCOMES",
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
            # Incluir variable en la key si aplica para distinguir PSI por variable
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
