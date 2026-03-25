"""
Tests de integración para MetricsCalculator.

Verifica que:
- El calculador lee targets desde META_VARIABLES (no lista hardcodeada)
- Cada target usa su propio lag para calcular origination_week
- Las métricas se escriben en FACT_METRICS_HISTORY con los nombres correctos
- No hay duplicados en re-ejecución
- s2 genera alerta de ordering violations (anomalía inyectada en fixtures)
"""

import pytest
from datetime import date

from mlmonitor.metrics.calculator import AlertEvaluator, MetricsCalculator
from mlmonitor.db.models import FactMetricsHistory, MetaVariables
from conftest import TARGET_NAME, TARGET_LAG, MODEL_ID, _week_date


class TestMetricsCalculator:
    def test_run_for_model_populates_history(
        self, session, current_week
    ):
        """run_for_model debe insertar filas en FACT_METRICS_HISTORY."""
        calc = MetricsCalculator(session)
        rows = calc.run_for_model(MODEL_ID, current_week)
        assert len(rows) > 0, "Debe insertar al menos 1 fila"

    def test_metrics_written_to_db(
        self, session, current_week, segment_ids
    ):
        """Las métricas deben quedar escritas en FACT_METRICS_HISTORY."""
        calc = MetricsCalculator(session)
        calc.run_for_model(MODEL_ID, current_week)

        reg_ids = list(segment_ids.values())
        db_rows = (
            session.query(FactMetricsHistory)
            .filter(
                FactMetricsHistory.model_registry_id.in_(reg_ids),
                FactMetricsHistory.calculation_week == current_week,
            )
            .count()
        )
        assert db_rows > 0

    def test_targets_read_from_db_not_hardcoded(
        self, session, current_week, segment_ids, metric_name_map
    ):
        """
        El calculador usa los targets de META_VARIABLES, no una lista hardcodeada.
        Verificar que existe una métrica gini_{TARGET_NAME} en FACT_METRICS_HISTORY.
        """
        calc = MetricsCalculator(session)
        calc.run_for_model(MODEL_ID, current_week)

        reg_id = segment_ids["s1"]
        metrics = calc.get_current_metrics_for_segment(reg_id, current_week, metric_name_map)

        gini_key = f"gini_{TARGET_NAME}"
        assert gini_key in metrics, (
            f"Métrica '{gini_key}' no encontrada. "
            f"Métricas disponibles: {[k for k in metrics if 'gini' in k]}"
        )

    def test_per_target_lag_used_for_origination_week(
        self, session, current_week, segment_ids, metric_name_map
    ):
        """
        Cada target usa su propio lag_semanas para determinar el origination_week.
        origination_week = current_week - lag_semanas
        Verificar que el details de la métrica gini tiene el origination_week correcto.
        """
        calc = MetricsCalculator(session)
        calc.run_for_model(MODEL_ID, current_week)

        reg_id = segment_ids["s1"]
        metrics = calc.get_current_metrics_for_segment(reg_id, current_week, metric_name_map)

        gini_key = f"gini_{TARGET_NAME}"
        if gini_key in metrics:
            details = metrics[gini_key].get("details", {})
            expected_origination_week = _week_date(0).isoformat()  # current_week - TARGET_LAG
            assert details.get("origination_week") == expected_origination_week, (
                f"origination_week esperado: {expected_origination_week}, "
                f"obtenido: {details.get('origination_week')}"
            )

    def test_s2_ordering_violation_alert(
        self, session, current_week, segment_ids, metric_name_map
    ):
        """s2 debe generar alerta de ordering violation (inversión bins 3 y 4)."""
        calc = MetricsCalculator(session)
        calc.run_for_model(MODEL_ID, current_week)

        reg_id = segment_ids["s2"]
        metrics = calc.get_current_metrics_for_segment(reg_id, current_week, metric_name_map)

        ov_key = f"ordering_violations_{TARGET_NAME}"
        assert ov_key in metrics, (
            f"Métrica '{ov_key}' no encontrada. "
            f"Disponibles: {[k for k in metrics if 'violation' in k]}"
        )
        assert metrics[ov_key]["value"] >= 1, (
            f"s2 debe tener ≥ 1 violación, obtenido: {metrics[ov_key]['value']}"
        )

    def test_all_segments_have_metrics(
        self, session, current_week, segment_ids, metric_name_map
    ):
        """Todos los segmentos deben tener métricas calculadas."""
        calc = MetricsCalculator(session)
        calc.run_for_model(MODEL_ID, current_week)

        for submodel_id, reg_id in segment_ids.items():
            metrics = calc.get_current_metrics_for_segment(
                reg_id, current_week, metric_name_map
            )
            assert len(metrics) > 0, (
                f"Segmento {submodel_id} (id={reg_id}) no tiene métricas calculadas"
            )

    def test_no_duplicate_metrics_on_rerun(
        self, session, current_week, segment_ids
    ):
        """Ejecutar el calculador dos veces no debe duplicar filas."""
        calc = MetricsCalculator(session)
        calc.run_for_model(MODEL_ID, current_week)

        reg_ids = list(segment_ids.values())
        count_before = (
            session.query(FactMetricsHistory)
            .filter(
                FactMetricsHistory.model_registry_id.in_(reg_ids),
                FactMetricsHistory.calculation_week == current_week,
            )
            .count()
        )

        calc.run_for_model(MODEL_ID, current_week)

        count_after = (
            session.query(FactMetricsHistory)
            .filter(
                FactMetricsHistory.model_registry_id.in_(reg_ids),
                FactMetricsHistory.calculation_week == current_week,
            )
            .count()
        )

        assert count_before == count_after, (
            f"Segunda ejecución creó duplicados: {count_before} → {count_after}"
        )

    def test_s2_psi_alert(
        self, session, current_week, segment_ids, metric_name_map
    ):
        """s2 num_var drifteada → alerta PSI WARNING o CRITICAL."""
        calc = MetricsCalculator(session)
        calc.run_for_model(MODEL_ID, current_week)

        reg_id = segment_ids["s2"]
        metrics = calc.get_current_metrics_for_segment(reg_id, current_week, metric_name_map)

        psi_alerts = {k: v for k, v in metrics.items() if "psi" in k and v.get("alert_flag", 0) > 0}
        assert len(psi_alerts) > 0, (
            f"s2 debe tener alertas PSI dado su drift. "
            f"Métricas PSI: {[(k, v['value']) for k, v in metrics.items() if 'psi' in k]}"
        )


class TestAlertEvaluator:
    def test_psi_critical_threshold(self, session, segment_ids):
        """PSI > 0.20 debe ser CRITICAL."""
        evaluator = AlertEvaluator(session)
        any_reg_id = next(iter(segment_ids.values()))
        flag, label = evaluator.evaluate("psi", 0.25, any_reg_id)
        assert flag == 2
        assert label == "CRITICAL"

    def test_psi_warning_threshold(self, session, segment_ids):
        """PSI entre 0.10 y 0.20 debe ser WARNING."""
        evaluator = AlertEvaluator(session)
        any_reg_id = next(iter(segment_ids.values()))
        flag, label = evaluator.evaluate("psi", 0.15, any_reg_id)
        assert flag == 1
        assert label == "WARNING"

    def test_psi_ok_threshold(self, session, segment_ids):
        """PSI < 0.10 debe ser OK."""
        evaluator = AlertEvaluator(session)
        any_reg_id = next(iter(segment_ids.values()))
        flag, label = evaluator.evaluate("psi", 0.05, any_reg_id)
        assert flag == 0
        assert label == "OK"

    def test_unknown_metric_returns_ok(self, session, segment_ids):
        """Métrica sin threshold definido retorna OK."""
        evaluator = AlertEvaluator(session)
        any_reg_id = next(iter(segment_ids.values()))
        flag, label = evaluator.evaluate("metrica_inexistente", 99.9, any_reg_id)
        assert flag == 0
        assert label == "OK"
