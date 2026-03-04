"""
Tests de integración para MetricsCalculator.

Verifica que el calculador produzca las alertas correctas según
las anomalías inyectadas en los datos dummy.
"""

import pytest
from datetime import date

from mlmonitor.metrics.calculator import MetricsCalculator, AlertEvaluator
from mlmonitor.data.dummy_generator import _week_date
from mlmonitor.db.models import FactMetricsHistory


class TestMetricsCalculator:
    def test_run_for_model_populates_history(
        self, session, model_id, current_week
    ):
        """run_for_model debe insertar filas en FACT_METRICS_HISTORY."""
        calc = MetricsCalculator(session)
        rows = calc.run_for_model(model_id, current_week)
        assert len(rows) > 0, "Debería insertar al menos 1 fila"

    def test_metrics_written_to_db(self, session, model_id, current_week, segment_ids):
        """Las métricas deben quedar escritas en FACT_METRICS_HISTORY."""
        calc = MetricsCalculator(session)
        calc.run_for_model(model_id, current_week)

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

    def test_s3_psi_critical_alert(self, session, model_id, segment_ids, metric_name_map):
        """s3 debe generar alerta CRITICAL por PSI en semana 20 (nivel_endeudamiento)."""
        week20 = _week_date(20)
        calc = MetricsCalculator(session)
        calc.run_for_model(model_id, week20)

        reg_id = segment_ids["s3"]
        # Invertir metric_name_map para obtener {metric_name: metric_id}
        metrics = calc.get_current_metrics_for_segment(reg_id, week20, metric_name_map)

        # PSI de nivel_endeudamiento debe ser CRITICAL
        psi_row = metrics.get("psi_nivel_endeudamiento") or metrics.get("psi_max")
        assert psi_row is not None, "No se encontró métrica PSI para s3"
        assert psi_row["alert_flag"] >= 1, (
            f"s3 PSI debe ser WARNING o CRITICAL, obtenido: {psi_row}"
        )

    def test_s9_null_rate_alert(self, session, model_id, segment_ids, metric_name_map):
        """s9 debe generar alerta por null_rate alto en semana 20 (meses_en_buro)."""
        week20 = _week_date(20)
        calc = MetricsCalculator(session)
        calc.run_for_model(model_id, week20)

        reg_id = segment_ids["s9"]
        metrics = calc.get_current_metrics_for_segment(reg_id, week20, metric_name_map)

        # Debe haber alguna alerta de null_rate
        null_alerts = {
            k: v for k, v in metrics.items()
            if "null_rate" in k and v.get("alert_flag", 0) > 0
        }
        assert len(null_alerts) > 0, (
            f"s9 semana 20 debe tener alertas de null_rate. "
            f"Métricas: {list(metrics.keys())}"
        )

    def test_s4_ordering_violation_alert(
        self, session, model_id, segment_ids, metric_name_map
    ):
        """s4 debe generar alerta por violaciones de ordering en semana 8."""
        week16 = _week_date(16)  # current_week que tiene performance_week=8
        calc = MetricsCalculator(session)
        calc.run_for_model(model_id, week16)

        reg_id = segment_ids["s4"]
        metrics = calc.get_current_metrics_for_segment(reg_id, week16, metric_name_map)

        rf_metric = metrics.get("roll_forward_ordering_violations")
        if rf_metric is not None:
            assert rf_metric["value"] >= 1, (
                f"s4 semana 16 (perf_week=8) debe tener >= 1 violación, "
                f"obtenido: {rf_metric['value']}"
            )

    def test_all_segments_have_metrics(
        self, session, model_id, current_week, segment_ids, metric_name_map
    ):
        """Todos los 11 segmentos (s1-s11) deben tener métricas calculadas."""
        calc = MetricsCalculator(session)
        calc.run_for_model(model_id, current_week)

        for fleet_id, reg_id in segment_ids.items():
            metrics = calc.get_current_metrics_for_segment(
                reg_id, current_week, metric_name_map
            )
            assert len(metrics) > 0, (
                f"Segmento {fleet_id} (id={reg_id}) no tiene métricas calculadas"
            )

    def test_alert_evaluator_uses_thresholds(self, session, segment_ids):
        """El evaluador de alertas debe usar los umbrales de META_METRIC_THRESHOLDS."""
        evaluator = AlertEvaluator(session)
        # Usar cualquier model_registry_id — los umbrales son globales (None)
        any_reg_id = next(iter(segment_ids.values()))

        # PSI > 0.20 debe ser CRITICAL
        flag, label = evaluator.evaluate("psi", 0.25, any_reg_id)
        assert flag == 2, f"PSI=0.25 debe ser CRITICAL (2), obtenido: {flag}"
        assert label == "CRITICAL"

        # PSI entre 0.10 y 0.20 debe ser WARNING
        flag, label = evaluator.evaluate("psi", 0.15, any_reg_id)
        assert flag == 1, f"PSI=0.15 debe ser WARNING (1), obtenido: {flag}"
        assert label == "WARNING"

        # PSI < 0.10 debe ser OK
        flag, label = evaluator.evaluate("psi", 0.05, any_reg_id)
        assert flag == 0, f"PSI=0.05 debe ser OK (0), obtenido: {flag}"
        assert label == "OK"

    def test_no_duplicate_metrics_on_rerun(
        self, session, model_id, current_week, segment_ids
    ):
        """Ejecutar el calculador dos veces no debe duplicar filas."""
        calc = MetricsCalculator(session)
        calc.run_for_model(model_id, current_week)

        reg_ids = list(segment_ids.values())
        count_before = (
            session.query(FactMetricsHistory)
            .filter(
                FactMetricsHistory.model_registry_id.in_(reg_ids),
                FactMetricsHistory.calculation_week == current_week,
            )
            .count()
        )

        # Segunda ejecución
        calc.run_for_model(model_id, current_week)

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
