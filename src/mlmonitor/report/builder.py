"""
ReportBuilder — Ensambla el contexto completo desde DB para el reporte.

Orquesta:
1. Lee métricas calculadas de FACT_METRICS_HISTORY
2. Construye SegmentMetrics para cada sub-scorecard
3. Llama al LLM para narrativas
4. Retorna el contexto completo para el renderer
"""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from mlmonitor.analyst.base import AnalysisContext, AnalysisResult, SegmentMetrics
from mlmonitor.db.models import (
    FactMetricsHistory,
    FactPerformanceOutcomes,
    MetaModelRegistry,
)
from mlmonitor.metrics.business_metrics import get_business_metrics_table

LAG_WEEKS = 8
STATUS_ORDER = {"CRITICAL": 0, "WARNING": 1, "OK": 2}


class ReportBuilder:
    """Construye el contexto del reporte desde la base de datos."""

    def __init__(self, session: Session):
        self.session = session

    def build(
        self,
        model_id: str,
        calculation_week: date,
        analyst=None,
    ) -> tuple[AnalysisContext, AnalysisResult | None]:
        """
        Construye el AnalysisContext y opcionalmente obtiene narrativas LLM.

        Args:
            model_id: ID del modelo
            calculation_week: semana de cálculo actual
            analyst: instancia de BaseAnalyst (None = sin LLM)

        Returns:
            (AnalysisContext, AnalysisResult | None)
        """
        performance_week = calculation_week - timedelta(weeks=LAG_WEEKS)

        # Obtener metadata del modelo
        model_regs = (
            self.session.query(MetaModelRegistry)
            .filter(
                MetaModelRegistry.model_id == model_id,
                MetaModelRegistry.valid_to.is_(None),
                MetaModelRegistry.is_active == 1,
            )
            .all()
        )
        model_name = (
            model_regs[0].model_name if model_regs else model_id
        )
        lag_semanas = model_regs[0].lag_semanas if model_regs else LAG_WEEKS

        # Construir SegmentMetrics por segmento
        segments = []
        for reg in model_regs:
            seg_metrics = self._build_segment_metrics(
                model_id=model_id,
                segment_id=reg.segment_id,
                segment_description=reg.segment_description or reg.segment_id,
                calculation_week=calculation_week,
                performance_week=performance_week,
            )
            segments.append(seg_metrics)

        # Ordenar por urgencia
        segments.sort(key=lambda s: STATUS_ORDER.get(s.overall_status, 99))

        # Fleet summary
        fleet_summary = {
            "total": len(segments),
            "ok": sum(1 for s in segments if s.overall_status == "OK"),
            "warning": sum(1 for s in segments if s.overall_status == "WARNING"),
            "critical": sum(1 for s in segments if s.overall_status == "CRITICAL"),
        }

        context = AnalysisContext(
            model_id=model_id,
            model_name=model_name,
            calculation_week=calculation_week,
            performance_week=performance_week,
            lag_semanas=lag_semanas,
            segments=segments,
            fleet_summary=fleet_summary,
        )

        # Llamada al LLM si hay analista
        result = None
        if analyst is not None:
            result = analyst.analyze_fleet(context)

        return context, result

    def _build_segment_metrics(
        self,
        model_id: str,
        segment_id: str,
        segment_description: str,
        calculation_week: date,
        performance_week: date,
    ) -> SegmentMetrics:
        """Construye SegmentMetrics desde FACT_METRICS_HISTORY."""
        metrics_rows = (
            self.session.query(FactMetricsHistory)
            .filter(
                FactMetricsHistory.model_id == model_id,
                FactMetricsHistory.segment_id == segment_id,
                FactMetricsHistory.calculation_week == calculation_week,
            )
            .all()
        )

        metrics_dict = {r.metric_name: r for r in metrics_rows}

        # PSI máximo
        psi_max = None
        psi_max_variable = None
        psi_max_row = metrics_dict.get("psi_max")
        if psi_max_row:
            psi_max = psi_max_row.metric_value
            psi_max_variable = (
                (psi_max_row.details or {}).get("max_variable", "")
            )

        # Gini / KS
        gini_row = metrics_dict.get("gini")
        gini = gini_row.metric_value if gini_row else None

        ks_row = metrics_dict.get("ks")
        ks = ks_row.metric_value if ks_row else None

        # Ordering violations
        rf_row = metrics_dict.get("roll_forward_ordering_violations")
        rf_violations = int(rf_row.metric_value or 0) if rf_row else 0

        pr_row = metrics_dict.get("payment_rate_ordering_violations")
        pr_violations = int(pr_row.metric_value or 0) if pr_row else 0

        # Null rate alerts
        null_rate_alerts = []
        for metric_name, row in metrics_dict.items():
            if metric_name.startswith("null_rate_") and row.alert_flag > 0:
                vname = metric_name.replace("null_rate_", "", 1)
                null_rate_alerts.append({
                    "variable": vname,
                    "rate": row.metric_value or 0.0,
                    "label": row.alert_label,
                    "flag": row.alert_flag,
                })

        # Active alerts (todas las métricas con flag > 0)
        active_alerts = []
        for metric_name, row in metrics_dict.items():
            if row.alert_flag > 0:
                active_alerts.append({
                    "metric": metric_name,
                    "value": row.metric_value,
                    "label": row.alert_label,
                    "flag": row.alert_flag,
                    "details": row.details,
                })
        active_alerts.sort(key=lambda x: -x["flag"])

        # Overall status
        if any(a["flag"] == 2 for a in active_alerts):
            overall_status = "CRITICAL"
        elif any(a["flag"] == 1 for a in active_alerts):
            overall_status = "WARNING"
        else:
            overall_status = "OK"

        # Business table (con lag)
        business_df = get_business_metrics_table(
            self.session, model_id, segment_id, performance_week
        )
        business_table = []
        if not business_df.empty:
            business_table = business_df.to_dict(orient="records")

        return SegmentMetrics(
            segment_id=segment_id,
            segment_description=segment_description,
            overall_status=overall_status,
            psi_max=psi_max,
            psi_max_variable=psi_max_variable,
            gini=gini,
            ks=ks,
            roll_forward_violations=rf_violations,
            payment_rate_violations=pr_violations,
            null_rate_alerts=null_rate_alerts,
            active_alerts=active_alerts,
            business_table=business_table,
        )
