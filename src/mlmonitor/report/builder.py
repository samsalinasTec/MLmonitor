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
from mlmonitor.data.bootstrap import PRIMARY_TARGET
from mlmonitor.db.models import (
    FactMetricsHistory,
    MetaMetricThresholds,
    MetaModelRegistry,
    MetaVariables,
)
from mlmonitor.metrics.business_metrics import get_business_metrics_table

STATUS_ORDER = {"CRITICAL": 0, "WARNING": 1, "OK": 2}
_LABEL_TO_FLAG = {"OK": 0, "WARNING": 1, "CRITICAL": 2}


def _classify_alert(
    key: str,
    metric_name: str,
    psi_max_variable: str | None,
    variable_descriptions: dict[str, str],
) -> tuple[str, str]:
    """Devuelve (metric_kind, display_label) para mostrar inline en el PDF.

    Reemplaza el identificador técnico por la descripción corta cuando aplica
    (PSI, null_rate). Para targets (gini/ks/ordering_violations) deja el nombre
    del target porque no tiene descripción humana en el diccionario.
    """
    if key == "psi_max":
        var = psi_max_variable or ""
        return "PSI Máximo", variable_descriptions.get(var, var) if var else "PSI Máximo"
    if key.startswith("psi_"):
        var = key[len("psi_"):]
        return "PSI", variable_descriptions.get(var, var)
    if key.startswith("null_rate_"):
        var = key[len("null_rate_"):]
        return "Null rate", variable_descriptions.get(var, var)
    if key.startswith("gini_"):
        return "Gini", key[len("gini_"):]
    if key.startswith("ks_"):
        return "KS", key[len("ks_"):]
    if key.startswith("ordering_violations_"):
        return "Violaciones de orden", key[len("ordering_violations_"):]
    return metric_name or key, key


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

        # Cobertura de performance por target — cada uno tiene su propio lag y cutoff.
        all_targets = []
        if model_regs:
            all_targets = (
                self.session.query(MetaVariables)
                .filter(
                    MetaVariables.model_registry_id == model_regs[0].id,
                    MetaVariables.variable_rol == "target",
                    MetaVariables.valid_to.is_(None),
                )
                .all()
            )
        for tv in all_targets:
            if tv.lag_semanas is None:
                raise ValueError(
                    f"Target '{tv.variable_name}' (registry_id={tv.model_registry_id}) has lag_semanas=NULL. "
                    "Every target must declare its lag explicitly in META_VARIABLES."
                )

        performance_coverage = []
        performance_weeks: dict[str, date] = {}
        for tv in sorted(all_targets, key=lambda v: v.lag_semanas):
            cutoff = calculation_week - timedelta(weeks=tv.lag_semanas)
            performance_coverage.append({
                "target": tv.variable_name,
                "lag": tv.lag_semanas,
                "cutoff_date": cutoff,
            })
            performance_weeks[tv.variable_name] = cutoff

        if all_targets:
            primary_lag = all_targets[0].lag_semanas
            performance_week = calculation_week - timedelta(weeks=primary_lag)
        else:
            primary_lag = None
            performance_week = calculation_week

        # Cargar mapa de métricas: {metric_id → metric_name}
        # + thresholds por segmento: {model_registry_id → {metric_name → {warn, crit, direction}}}
        threshold_rows = (
            self.session.query(MetaMetricThresholds)
            .filter(MetaMetricThresholds.valid_to.is_(None))
            .all()
        )
        metric_name_map: dict[int, str] = {r.id: r.metric_name for r in threshold_rows}
        thresholds_by_segment: dict[int, dict[str, dict]] = {}
        for r in threshold_rows:
            if r.model_registry_id is not None:
                seg_th = thresholds_by_segment.setdefault(r.model_registry_id, {})
                seg_th[r.metric_name] = {
                    "warn": r.warning_threshold,
                    "crit": r.critical_threshold,
                    "direction": r.direction or "higher_worse",
                }

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
            variable_desc_map = {
                v.variable_name: v.description
                for v in var_rows
                if v.variable_rol == "input" and v.description
            }
            target_vars = [v for v in var_rows if v.variable_rol == "target"]

            seg_metrics = self._build_segment_metrics(
                model_registry_id=reg.id,
                segment_id=reg.submodel_id,
                segment_description=reg.model_description or reg.submodel_id,
                variable_map=variable_map,
                variable_descriptions=variable_desc_map,
                target_vars=target_vars,
                metric_name_map=metric_name_map,
                thresholds=thresholds_by_segment.get(reg.id, {}),
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

        active_target_names = {tv.variable_name for tv in all_targets}
        if PRIMARY_TARGET in active_target_names:
            resolved_primary_target = PRIMARY_TARGET
        elif active_target_names:
            mid_idx = len(performance_coverage) // 2
            resolved_primary_target = performance_coverage[mid_idx]["target"]
        else:
            resolved_primary_target = PRIMARY_TARGET

        context = AnalysisContext(
            model_id=model_id,
            model_name=model_name,
            calculation_week=calculation_week,
            performance_week=performance_week,
            lag_semanas=primary_lag,
            segments=segments,
            fleet_summary=fleet_summary,
            total_submodels=len(model_regs),
            performance_coverage=performance_coverage,
            performance_weeks=performance_weeks,
            primary_target=resolved_primary_target,
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
        variable_descriptions: dict[str, str],
        target_vars: list,
        metric_name_map: dict[int, str],
        thresholds: dict[str, dict],
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
                mname = metric_name_map.get(row.metric_id, "")
                th = thresholds.get(mname, {})
                metric_kind, display_label = _classify_alert(
                    key, mname, psi_max_variable, variable_descriptions
                )
                active_alerts.append({
                    "metric": key,
                    "metric_kind": metric_kind,
                    "display_label": display_label,
                    "value": row.metric_value,
                    "label": row.alert_label,
                    "flag": _flag(row),
                    "details": row.details,
                    "warn_threshold": th.get("warn"),
                    "crit_threshold": th.get("crit"),
                })
        active_alerts.sort(key=lambda x: -x["flag"])

        # Overall status
        if any(a["flag"] == 2 for a in active_alerts):
            overall_status = "CRITICAL"
        elif any(a["flag"] == 1 for a in active_alerts):
            overall_status = "WARNING"
        else:
            overall_status = "OK"

        # Business table — cada target tiene su propio origination_week (calculado internamente)
        business_df = get_business_metrics_table(
            self.session, model_registry_id, calculation_week
        )
        business_table = []
        if not business_df.empty:
            # NaN → None para que el fallback `or 0` del template aplique.
            # `.astype(object)` antes de `.where(...)` porque en columnas float64 el NaN
            # no se reemplaza (dtype no admite None).
            business_table = (
                business_df.astype(object)
                .where(business_df.notna(), None)
                .to_dict(orient="records")
            )

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
            thresholds=thresholds,
            variable_descriptions=variable_descriptions,
        )
