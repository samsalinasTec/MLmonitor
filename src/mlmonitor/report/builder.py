"""
ReportBuilder — Ensambla el contexto completo desde DB para el reporte.

Orquesta:
1. Lee métricas calculadas de FACT_METRICS_HISTORY
2. Construye SegmentMetrics para cada sub-scorecard
3. Llama al LLM para narrativas
4. Retorna el contexto completo para el renderer

Los targets y sus lags se leen dinámicamente desde META_VARIABLES.
"""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from mlmonitor.analyst.base import AnalysisContext, AnalysisResult, SegmentMetrics
from mlmonitor.db.models import (
    FactMetricsHistory,
    MetaMetricThresholds,
    MetaModelRegistry,
    MetaVariables,
)
from mlmonitor.metrics.business_metrics import get_business_metrics_table

STATUS_ORDER = {"CRITICAL": 0, "WARNING": 1, "OK": 2}
_LABEL_TO_FLAG = {"OK": 0, "WARNING": 1, "CRITICAL": 2}


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
        # Obtener metadata del modelo (un registro por submodel_id)
        model_regs = (
            self.session.query(MetaModelRegistry)
            .filter(
                MetaModelRegistry.model_id == model_id,
                MetaModelRegistry.valid_to.is_(None),
            )
            .all()
        )
        model_name = model_regs[0].model_name if model_regs else model_id

        # Determinar performance_week de display usando el primer target del primer segmento.
        # Fallback: MetaModelRegistry.lag_semanas o 8 semanas.
        primary_lag = (model_regs[0].lag_semanas or 8) if model_regs else 8
        if model_regs:
            first_target = (
                self.session.query(MetaVariables)
                .filter(
                    MetaVariables.model_registry_id == model_regs[0].id,
                    MetaVariables.variable_rol == "target",
                    MetaVariables.valid_to.is_(None),
                )
                .first()
            )
            if first_target and first_target.lag_semanas:
                primary_lag = first_target.lag_semanas
        performance_week = calculation_week - timedelta(weeks=primary_lag)

        # Cargar mapa de métricas: {metric_id → metric_name}
        threshold_rows = (
            self.session.query(MetaMetricThresholds)
            .filter(MetaMetricThresholds.valid_to.is_(None))
            .all()
        )
        metric_name_map: dict[int, str] = {r.id: r.metric_name for r in threshold_rows}

        # Construir SegmentMetrics por segmento
        segments = []
        for reg in model_regs:
            # Cargar todas las variables del segmento
            var_rows = (
                self.session.query(MetaVariables)
                .filter(
                    MetaVariables.model_registry_id == reg.id,
                    MetaVariables.valid_to.is_(None),
                )
                .all()
            )
            variable_map = {v.id: v.variable_name for v in var_rows if v.variable_rol != "target"}
            target_vars = [v for v in var_rows if v.variable_rol == "target"]

            seg_metrics = self._build_segment_metrics(
                model_registry_id=reg.id,
                segment_id=reg.submodel_id,
                segment_description=reg.model_description or reg.submodel_id,
                variable_map=variable_map,
                target_vars=target_vars,
                metric_name_map=metric_name_map,
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
            lag_semanas=primary_lag,
            segments=segments,
            fleet_summary=fleet_summary,
            total_submodels=len(model_regs),
        )

        # Llamada al LLM si hay analista
        result = None
        if analyst is not None:
            result = analyst.analyze_fleet(context)

        return context, result

    def _build_segment_metrics(
        self,
        model_registry_id: int,
        segment_id: str,
        segment_description: str,
        variable_map: dict[int, str],
        target_vars: list,
        metric_name_map: dict[int, str],
        calculation_week: date,
        performance_week: date,
    ) -> SegmentMetrics:
        """Construye SegmentMetrics desde FACT_METRICS_HISTORY."""
        metrics_rows = (
            self.session.query(FactMetricsHistory)
            .filter(
                FactMetricsHistory.model_registry_id == model_registry_id,
                FactMetricsHistory.calculation_week == calculation_week,
            )
            .all()
        )

        # Construir dict de métricas con nombres resueltos
        metrics_dict: dict[str, FactMetricsHistory] = {}
        for row in metrics_rows:
            mname = metric_name_map.get(row.metric_id, f"metric_{row.metric_id}")
            details = row.details or {}

            if mname == "psi" and details.get("is_max_psi"):
                key = "psi_max"
            elif mname == "psi" and row.variable_id is not None:
                vname = variable_map.get(row.variable_id, str(row.variable_id))
                key = f"psi_{vname}"
            elif mname == "null_rate" and row.variable_id is not None:
                vname = variable_map.get(row.variable_id, str(row.variable_id))
                key = f"null_rate_{vname}"
            else:
                key = mname

            metrics_dict[key] = row

        def _flag(row: FactMetricsHistory) -> int:
            return _LABEL_TO_FLAG.get(row.alert_label, 0)

        # PSI máximo
        psi_max = None
        psi_max_variable = None
        psi_max_row = metrics_dict.get("psi_max")
        if psi_max_row:
            psi_max = psi_max_row.metric_value
            psi_max_variable = (psi_max_row.details or {}).get("max_variable", "")

        # Gini / KS / ordering violations — un valor por variable target
        gini: dict[str, float | None] = {}
        ks: dict[str, float | None] = {}
        ordering_violations: dict[str, int] = {}
        for target in target_vars:
            tname = target.variable_name
            gini_row = metrics_dict.get(f"gini_{tname}")
            gini[tname] = gini_row.metric_value if gini_row else None

            ks_row = metrics_dict.get(f"ks_{tname}")
            ks[tname] = ks_row.metric_value if ks_row else None

            ov_row = metrics_dict.get(f"ordering_violations_{tname}")
            ordering_violations[tname] = int(ov_row.metric_value or 0) if ov_row else 0

        # Null rate alerts
        null_rate_alerts = []
        for key, row in metrics_dict.items():
            if key.startswith("null_rate_") and _flag(row) > 0:
                vname = key.replace("null_rate_", "", 1)
                null_rate_alerts.append({
                    "variable": vname,
                    "rate": row.metric_value or 0.0,
                    "label": row.alert_label,
                    "flag": _flag(row),
                })

        # Active alerts (todas las métricas con flag > 0)
        active_alerts = []
        for key, row in metrics_dict.items():
            if _flag(row) > 0:
                active_alerts.append({
                    "metric": key,
                    "value": row.metric_value,
                    "label": row.alert_label,
                    "flag": _flag(row),
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

        # Business table — score_week es la semana de performance del target primario
        business_df = get_business_metrics_table(
            self.session, model_registry_id, performance_week
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
            ordering_violations=ordering_violations,
            null_rate_alerts=null_rate_alerts,
            active_alerts=active_alerts,
            business_table=business_table,
        )
