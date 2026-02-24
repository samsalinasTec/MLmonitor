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
)
from mlmonitor.metrics.business_metrics import (
    get_business_metrics_table,
    get_payment_rate_violations,
    get_roll_forward_violations,
)
from mlmonitor.metrics.performance import get_gini_ks_for_segment
from mlmonitor.metrics.psi import get_max_psi, get_null_rates, get_psi_for_all_variables

LAG_WEEKS = 8


class AlertEvaluator:
    """Evalúa alertas usando los umbrales de META_METRIC_THRESHOLDS."""

    def __init__(self, session: Session):
        self._thresholds_cache: dict = {}
        self._load_thresholds(session)

    def _load_thresholds(self, session: Session) -> None:
        rows = (
            session.query(MetaMetricThresholds)
            .filter(MetaMetricThresholds.valid_to.is_(None))
            .all()
        )
        for r in rows:
            key = (r.metric_name, r.model_id_override)
            self._thresholds_cache[key] = r

    def get_threshold(self, metric_name: str, model_id: str) -> MetaMetricThresholds | None:
        """Busca umbral específico del modelo; si no existe, usa global (model_id_override=None)."""
        specific = self._thresholds_cache.get((metric_name, model_id))
        if specific:
            return specific
        return self._thresholds_cache.get((metric_name, None))

    def evaluate(
        self, metric_name: str, value: float, model_id: str
    ) -> tuple[int, str]:
        """
        Retorna (alert_flag, alert_label):
          0 = OK, 1 = WARNING, 2 = CRITICAL
        """
        if value is None:
            return 0, "OK"

        threshold = self.get_threshold(metric_name, model_id)
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
                MetaModelRegistry.is_active == 1,
            )
            .all()
        )

        all_rows: list[FactMetricsHistory] = []

        for seg in segments:
            segment_id = seg.segment_id
            rows = self._calculate_segment_metrics(
                model_id=model_id,
                segment_id=segment_id,
                current_week=current_week,
                performance_week=performance_week,
            )
            all_rows.extend(rows)

        # Insertar en DB (ignorar conflictos por duplicado)
        for row in all_rows:
            existing = (
                self.session.query(FactMetricsHistory)
                .filter(
                    FactMetricsHistory.model_id == row.model_id,
                    FactMetricsHistory.segment_id == row.segment_id,
                    FactMetricsHistory.calculation_week == row.calculation_week,
                    FactMetricsHistory.metric_name == row.metric_name,
                )
                .first()
            )
            if existing is None:
                self.session.add(row)

        self.session.flush()
        return all_rows

    def _calculate_segment_metrics(
        self,
        model_id: str,
        segment_id: str,
        current_week: date,
        performance_week: date,
    ) -> list[FactMetricsHistory]:
        rows = []

        # --- PSI por variable ---
        psi_by_var = get_psi_for_all_variables(
            self.session, model_id, segment_id, current_week
        )
        for vname, psi_val in psi_by_var.items():
            flag, label = self.evaluator.evaluate("psi", psi_val, model_id)
            rows.append(FactMetricsHistory(
                model_id=model_id,
                segment_id=segment_id,
                calculation_week=current_week,
                metric_name=f"psi_{vname}",
                metric_value=round(psi_val, 4),
                alert_flag=flag,
                alert_label=label,
                details={"variable": vname},
            ))

        # PSI máximo del segmento
        max_psi, max_var = get_max_psi(psi_by_var)
        flag, label = self.evaluator.evaluate("psi", max_psi, model_id)
        rows.append(FactMetricsHistory(
            model_id=model_id,
            segment_id=segment_id,
            calculation_week=current_week,
            metric_name="psi_max",
            metric_value=round(max_psi, 4),
            alert_flag=flag,
            alert_label=label,
            details={"max_variable": max_var},
        ))

        # --- Tasas de nulos ---
        null_rates = get_null_rates(self.session, model_id, segment_id, current_week)
        for vname, null_rate in null_rates.items():
            flag, label = self.evaluator.evaluate("null_rate", null_rate, model_id)
            rows.append(FactMetricsHistory(
                model_id=model_id,
                segment_id=segment_id,
                calculation_week=current_week,
                metric_name=f"null_rate_{vname}",
                metric_value=round(null_rate, 4),
                alert_flag=flag,
                alert_label=label,
                details={"variable": vname},
            ))

        # --- Gini y KS (con lag de 8 semanas) ---
        perf_metrics = get_gini_ks_for_segment(
            self.session, model_id, segment_id, performance_week
        )
        if perf_metrics.get("gini") is not None:
            flag, label = self.evaluator.evaluate("gini", perf_metrics["gini"], model_id)
            rows.append(FactMetricsHistory(
                model_id=model_id,
                segment_id=segment_id,
                calculation_week=current_week,
                metric_name="gini",
                metric_value=perf_metrics["gini"],
                alert_flag=flag,
                alert_label=label,
                details={"performance_week": performance_week.isoformat()},
            ))

        if perf_metrics.get("ks") is not None:
            flag, label = self.evaluator.evaluate("ks", perf_metrics["ks"], model_id)
            rows.append(FactMetricsHistory(
                model_id=model_id,
                segment_id=segment_id,
                calculation_week=current_week,
                metric_name="ks",
                metric_value=perf_metrics["ks"],
                alert_flag=flag,
                alert_label=label,
                details={"performance_week": performance_week.isoformat()},
            ))

        # --- Violaciones de ordering (con lag) ---
        rf_violations = get_roll_forward_violations(
            self.session, model_id, segment_id, performance_week
        )
        n_rf = rf_violations.get("violations", 0)
        flag, label = self.evaluator.evaluate(
            "roll_forward_ordering_violations", n_rf, model_id
        )
        rows.append(FactMetricsHistory(
            model_id=model_id,
            segment_id=segment_id,
            calculation_week=current_week,
            metric_name="roll_forward_ordering_violations",
            metric_value=float(n_rf),
            alert_flag=flag,
            alert_label=label,
            details={
                "performance_week": performance_week.isoformat(),
                "violation_pairs": rf_violations.get("violation_pairs", []),
            },
        ))

        pr_violations = get_payment_rate_violations(
            self.session, model_id, segment_id, performance_week
        )
        n_pr = pr_violations.get("violations", 0)
        flag, label = self.evaluator.evaluate(
            "payment_rate_ordering_violations", n_pr, model_id
        )
        rows.append(FactMetricsHistory(
            model_id=model_id,
            segment_id=segment_id,
            calculation_week=current_week,
            metric_name="payment_rate_ordering_violations",
            metric_value=float(n_pr),
            alert_flag=flag,
            alert_label=label,
            details={
                "performance_week": performance_week.isoformat(),
                "violation_pairs": pr_violations.get("violation_pairs", []),
            },
        ))

        return rows

    def get_current_metrics_for_segment(
        self, model_id: str, segment_id: str, calculation_week: date
    ) -> dict[str, dict]:
        """Lee las métricas calculadas de FACT_METRICS_HISTORY para un segmento."""
        rows = (
            self.session.query(FactMetricsHistory)
            .filter(
                FactMetricsHistory.model_id == model_id,
                FactMetricsHistory.segment_id == segment_id,
                FactMetricsHistory.calculation_week == calculation_week,
            )
            .all()
        )
        return {
            r.metric_name: {
                "value": r.metric_value,
                "alert_flag": r.alert_flag,
                "alert_label": r.alert_label,
                "details": r.details,
            }
            for r in rows
        }
