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

from config.settings import settings
from mlmonitor.analyst.base import AnalysisContext, AnalysisResult, SegmentMetrics
from mlmonitor.db.models import (
    FactMetricsHistory,
    MetaMetricThresholds,
    MetaModelRegistry,
    MetaVariables,
)
from mlmonitor.metrics.business_metrics import get_business_metrics_table
from mlmonitor.metrics.decile_metrics import load_per_target_deciles
from mlmonitor.metrics.performance import get_gini_ks_global
from mlmonitor.report.charts import (
    render_consolidated_decile_chart,
    render_per_target_decile_chart,
)

_LABEL_TO_FLAG = {"OK": 0, "WARNING": 1, "CRITICAL": 2}
STATUS_DISPLAY_ES = {"OK": "OK", "WARNING": "ADVERTENCIA", "CRITICAL": "CRÍTICO"}


def _segment_sort_key(segment_id: str) -> int:
    """Convierte 's1', 's10' a 1, 10 para sort numérico (no alfabético).

    Fallback a 999 para IDs no estándar — los deja al final sin romper el sort.
    """
    if segment_id.startswith("s") and segment_id[1:].isdigit():
        return int(segment_id[1:])
    return 999


def _is_headline_alert(alert_key: str, primary_target: str) -> bool:
    """Métricas 'headline': cualquier crítica eleva el segmento a CRÍTICO inmediatamente.

    PSI del score, Gini y KS del target primario miden directamente la salud
    del modelo — no se cuentan, escalan en automático.
    """
    return alert_key in {
        "psi_score",
        f"gini_{primary_target}",
        f"ks_{primary_target}",
    }


def _aggregate_status(
    active_alerts: list[dict],
    primary_target: str,
) -> tuple[str, str]:
    """Calcula el estado agregado del segmento + razón corta.

    Reglas (ver docs/architecture/data_model.md §4.7):
    1. 1+ headline crítico → CRITICAL (inmediato)
    2. ≥ status_crit_count_to_critical (5) agregables críticos → CRITICAL
    3. ≥ status_crit_count_to_warning (3) agregables críticos,
       O headline en WARNING,
       O ≥ status_warn_count_to_warning (8) agregables warnings → WARNING
    4. Resto → OK

    Headlines: psi_score, gini_<primary_target>, ks_<primary_target>.
    psi_max se excluye del conteo para no doble-contar (ya está cubierto
    por psi_<variable> individuales y por psi_score).
    Umbrales configurables en config/settings.py.
    """
    relevant = [a for a in active_alerts if a.get("metric") != "psi_max"]

    headline_crit = [
        a for a in relevant
        if a["flag"] == 2 and _is_headline_alert(a["metric"], primary_target)
    ]
    if headline_crit:
        kinds = ", ".join(sorted({a["metric_kind"] for a in headline_crit}))
        return "CRITICAL", f"headline crítico ({kinds})"

    headline_warn = [
        a for a in relevant
        if a["flag"] == 1 and _is_headline_alert(a["metric"], primary_target)
    ]
    agg_crits = [
        a for a in relevant
        if a["flag"] == 2 and not _is_headline_alert(a["metric"], primary_target)
    ]
    agg_warns = [
        a for a in relevant
        if a["flag"] == 1 and not _is_headline_alert(a["metric"], primary_target)
    ]

    if len(agg_crits) >= settings.status_crit_count_to_critical:
        return (
            "CRITICAL",
            f"{len(agg_crits)} alertas críticas agregadas "
            f"(umbral: ≥{settings.status_crit_count_to_critical})",
        )

    triggers = []
    if len(agg_crits) >= settings.status_crit_count_to_warning:
        triggers.append(f"{len(agg_crits)} crítica(s) agregada(s)")
    if headline_warn:
        triggers.append(f"{len(headline_warn)} headline en advertencia")
    if len(agg_warns) >= settings.status_warn_count_to_warning:
        triggers.append(f"{len(agg_warns)} advertencia(s) agregadas")

    if triggers:
        return "WARNING", "; ".join(triggers)
    return "OK", "sin alertas relevantes"


def _build_severity_legend() -> list[dict]:
    """Genera la leyenda de las reglas de _aggregate_status, leyendo umbrales
    actuales de config.settings.

    Mantener sincronizado con _aggregate_status: cualquier cambio a las reglas
    debe reflejarse aquí. Los counts vienen de settings para que la leyenda
    impresa siempre refleje la configuración real.
    """
    return [
        {
            "status": "CRITICAL",
            "label": STATUS_DISPLAY_ES["CRITICAL"],
            "rules": [
                "1+ alerta crítica en métrica headline (PSI del score, "
                "Gini o KS del target primario), o",
                f"≥{settings.status_crit_count_to_critical} alertas críticas "
                "agregadas (PSI por variable, null_rate, gini/ks de targets "
                "secundarios, violaciones de orden).",
            ],
        },
        {
            "status": "WARNING",
            "label": STATUS_DISPLAY_ES["WARNING"],
            "rules": [
                f"≥{settings.status_crit_count_to_warning} alertas críticas "
                "agregadas, o",
                "1+ headline en advertencia, o",
                f"≥{settings.status_warn_count_to_warning} alertas en "
                "advertencia agregadas.",
            ],
        },
        {
            "status": "OK",
            "label": STATUS_DISPLAY_ES["OK"],
            "rules": ["Ninguna de las condiciones anteriores se cumple."],
        },
    ]


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

        # Resolver primary_target ANTES del loop para poder pasarlo a cada segmento.
        # Fuente de verdad: META_MODEL_REGISTRY.primary_target_variable.
        # Fallback (cuando la columna está NULL o el target nombrado no está activo):
        # target con lag mediano. Para "ningún target activo" queda string vacío.
        active_target_names = {tv.variable_name for tv in all_targets}
        registered_primary = model_regs[0].primary_target_variable if model_regs else None

        if registered_primary and registered_primary in active_target_names:
            resolved_primary_target = registered_primary
        elif active_target_names:
            mid_idx = len(performance_coverage) // 2
            resolved_primary_target = performance_coverage[mid_idx]["target"]
        else:
            resolved_primary_target = ""

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
                primary_target=resolved_primary_target,
            )
            segments.append(seg_metrics)

        # Orden numérico estricto (s1, s2, …, s10, s11) — no por urgencia.
        segments.sort(key=lambda s: _segment_sort_key(s.segment_id))

        # Fleet summary
        fleet_summary = {
            "total": len(segments),
            "ok": sum(1 for s in segments if s.overall_status == "OK"),
            "warning": sum(1 for s in segments if s.overall_status == "WARNING"),
            "critical": sum(1 for s in segments if s.overall_status == "CRITICAL"),
        }

        # Gini/KS global por target — combina la población de TODOS los segmentos
        # para una origination_week (= calculation_week − lag). Score invertido
        # por crédito según el score_max de su segmento (robusto a futuros
        # modelos donde varíe; hoy BAZBOOST_V1 lo tiene uniforme).
        score_max_by_registry = {r.id: (r.score_max or 1000) for r in model_regs}
        global_performance: list[dict] = []
        for tv in sorted(all_targets, key=lambda v: v.lag_semanas):
            origination_week = calculation_week - timedelta(weeks=tv.lag_semanas)
            gk = get_gini_ks_global(
                self.session,
                model_id,
                origination_week,
                tv.variable_name,
                score_max_by_registry,
            )
            global_performance.append({
                "target": tv.variable_name,
                "origination_week": origination_week,
                **gk,
            })

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
            severity_legend=_build_severity_legend(),
            global_performance=global_performance,
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
        primary_target: str,
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

        # Overall status — ver _aggregate_status para la lógica detallada
        overall_status, status_reason = _aggregate_status(active_alerts, primary_target)

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

        # Gráficas de deciles reales (qcut) — consolidada + por target.
        decile_charts = self._build_decile_charts(
            model_registry_id=model_registry_id,
            segment_id=segment_id,
            target_vars=target_vars,
            calculation_week=calculation_week,
            primary_target=primary_target,
        )

        return SegmentMetrics(
            segment_id=segment_id,
            segment_description=segment_description,
            overall_status=overall_status,
            status_reason=status_reason,
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
            decile_charts=decile_charts,
        )

    def _build_decile_charts(
        self,
        model_registry_id: int,
        segment_id: str,
        target_vars: list,
        calculation_week: date,
        primary_target: str,
    ) -> dict:
        """Genera las dos gráficas de deciles leyendo FACT_DECILES_HISTORY.

        La persistencia la hace el calculator (un solo cálculo por segmento por
        semana). Aquí solo se renderizan. La gráfica "consolidada" se construye
        a partir de los deciles del target primario más las event_rates de los
        otros targets en los mismos rangos de score (best-effort: si los
        score_min/max no alinean, se omiten esos targets adicionales).
        """
        pt_data = load_per_target_deciles(
            session=self.session,
            model_registry_id=model_registry_id,
            calculation_week=calculation_week,
        )

        # Per-target payload: shape compatible con render_per_target_decile_chart.
        # Targets sin deciles persistidos quedan marcados como no disponibles.
        per_target_full: dict[str, dict] = {}
        for t in target_vars:
            tname = t.variable_name
            if tname in pt_data:
                per_target_full[tname] = pt_data[tname]
            else:
                cohort = calculation_week - timedelta(weeks=t.lag_semanas or 0)
                per_target_full[tname] = {
                    "cohort_week": cohort,
                    "cohort_window_start": cohort,
                    "cohort_window_end": cohort,
                    "decile_table": None,
                    "available": False,
                    "reason": "Sin deciles persistidos para esta cohorte",
                }

        pt_available = any(p["available"] for p in per_target_full.values())
        pt_payload = {
            "available": pt_available,
            "img_b64": None,
            "reason": None if pt_available else "Ningún target tiene cohorte madura disponible",
            "targets": [
                {
                    "name": t,
                    "available": p["available"],
                    "cohort_week": p["cohort_week"].isoformat(),
                }
                for t, p in per_target_full.items()
            ],
        }
        if pt_available:
            pt_payload["img_b64"] = render_per_target_decile_chart(
                per_target=per_target_full,
                segment_id=segment_id,
            )

        # Consolidada: anclada en los deciles del target primario.
        primary_entry = pt_data.get(primary_target)
        if primary_entry is not None and primary_entry.get("available"):
            base_table = primary_entry["decile_table"]
            rates_by_target = {
                primary_target: base_table["event_rate"].tolist(),
            }
            consolidated_payload = {
                "available": True,
                "img_b64": render_consolidated_decile_chart(
                    decile_table=base_table,
                    rates_by_target=rates_by_target,
                    cohort_week=primary_entry["cohort_window_end"],
                    primary_target=primary_target,
                    segment_id=segment_id,
                ),
                "reason": None,
                "cohort_week": primary_entry["cohort_window_end"].isoformat(),
                "missing_targets": [
                    t.variable_name for t in target_vars
                    if t.variable_name != primary_target
                    and (t.lag_semanas or 0) > (
                        next(
                            (x.lag_semanas or 0 for x in target_vars
                             if x.variable_name == primary_target),
                            0,
                        )
                    )
                ],
            }
        else:
            cohort_fallback = calculation_week - timedelta(
                weeks=next(
                    (t.lag_semanas or 0 for t in target_vars
                     if t.variable_name == primary_target),
                    0,
                )
            )
            consolidated_payload = {
                "available": False,
                "img_b64": None,
                "reason": "Sin deciles persistidos del target primario",
                "cohort_week": cohort_fallback.isoformat(),
                "missing_targets": [],
            }

        return {"consolidated": consolidated_payload, "per_target": pt_payload}
