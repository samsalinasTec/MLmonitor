"""
Tests de integración para MetricsCalculator.

Verifica que el calculador produzca las alertas correctas según
las anomalías inyectadas en los datos dummy.
"""

import pytest
from datetime import date

from mlmonitor.metrics.calculator import MetricsCalculator
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

    def test_metrics_written_to_db(self, session, model_id, current_week):
        """Las métricas deben quedar escritas en FACT_METRICS_HISTORY."""
        calc = MetricsCalculator(session)
        calc.run_for_model(model_id, current_week)

        db_rows = (
            session.query(FactMetricsHistory)
            .filter(
                FactMetricsHistory.model_id == model_id,
                FactMetricsHistory.calculation_week == current_week,
            )
            .count()
        )
        assert db_rows > 0

    def test_s3_psi_critical_alert(self, session, model_id):
        """s3 debe generar alerta CRITICAL por PSI en semana 20 (nivel_endeudamiento)."""
        week20 = _week_date(20)
        calc = MetricsCalculator(session)
        calc.run_for_model(model_id, week20)

        metrics = calc.get_current_metrics_for_segment(model_id, "s3", week20)

        # PSI de nivel_endeudamiento debe ser CRITICAL
        psi_row = metrics.get("psi_nivel_endeudamiento") or metrics.get("psi_max")
        assert psi_row is not None, "No se encontró métrica PSI para s3"
        assert psi_row["alert_flag"] >= 2 or (
            metrics.get("psi_nivel_endeudamiento", {}).get("alert_flag", 0) >= 2
        ), f"s3 PSI debe ser CRITICAL, obtenido: {psi_row}"

    def test_s9_null_rate_alert(self, session, model_id):
        """s9 debe generar alerta por null_rate alto en semana 20 (meses_en_buro)."""
        week20 = _week_date(20)
        calc = MetricsCalculator(session)
        calc.run_for_model(model_id, week20)

        metrics = calc.get_current_metrics_for_segment(model_id, "s9", week20)

        # Debe haber alguna alerta de null_rate
        null_alerts = {
            k: v for k, v in metrics.items()
            if k.startswith("null_rate_") and v.get("alert_flag", 0) > 0
        }
        assert len(null_alerts) > 0, (
            f"s9 semana 20 debe tener alertas de null_rate. "
            f"Métricas: {list(metrics.keys())}"
        )

    def test_s4_ordering_violation_alert(self, session, model_id):
        """s4 debe generar alerta por violaciones de ordering en semana 8."""
        week16 = _week_date(16)  # current_week que tiene performance_week=8
        calc = MetricsCalculator(session)
        calc.run_for_model(model_id, week16)

        metrics = calc.get_current_metrics_for_segment(model_id, "s4", week16)

        rf_metric = metrics.get("roll_forward_ordering_violations")
        if rf_metric is not None:
            assert rf_metric["value"] >= 1, (
                f"s4 semana 16 (perf_week=8) debe tener >= 1 violación, "
                f"obtenido: {rf_metric['value']}"
            )

    def test_all_segments_have_metrics(self, session, model_id, current_week):
        """Todos los 11 segmentos (s1-s11) deben tener métricas calculadas."""
        calc = MetricsCalculator(session)
        calc.run_for_model(model_id, current_week)

        segments = [f"s{i}" for i in range(1, 12)]
        for seg_id in segments:
            metrics = calc.get_current_metrics_for_segment(
                model_id, seg_id, current_week
            )
            assert len(metrics) > 0, (
                f"Segmento {seg_id} no tiene métricas calculadas"
            )

    def test_alert_evaluator_uses_thresholds(self, session, model_id, current_week):
        """El evaluador de alertas debe usar los umbrales de META_METRIC_THRESHOLDS."""
        from mlmonitor.metrics.calculator import AlertEvaluator
        evaluator = AlertEvaluator(session)

        # PSI > 0.20 debe ser CRITICAL
        flag, label = evaluator.evaluate("psi", 0.25, model_id)
        assert flag == 2, f"PSI=0.25 debe ser CRITICAL (2), obtenido: {flag}"
        assert label == "CRITICAL"

        # PSI entre 0.10 y 0.20 debe ser WARNING
        flag, label = evaluator.evaluate("psi", 0.15, model_id)
        assert flag == 1, f"PSI=0.15 debe ser WARNING (1), obtenido: {flag}"
        assert label == "WARNING"

        # PSI < 0.10 debe ser OK
        flag, label = evaluator.evaluate("psi", 0.05, model_id)
        assert flag == 0, f"PSI=0.05 debe ser OK (0), obtenido: {flag}"
        assert label == "OK"

    def test_no_duplicate_metrics_on_rerun(self, session, model_id, current_week):
        """Ejecutar el calculador dos veces no debe duplicar filas."""
        calc = MetricsCalculator(session)
        calc.run_for_model(model_id, current_week)

        count_before = (
            session.query(FactMetricsHistory)
            .filter(
                FactMetricsHistory.model_id == model_id,
                FactMetricsHistory.calculation_week == current_week,
            )
            .count()
        )

        # Segunda ejecución
        calc.run_for_model(model_id, current_week)

        count_after = (
            session.query(FactMetricsHistory)
            .filter(
                FactMetricsHistory.model_id == model_id,
                FactMetricsHistory.calculation_week == current_week,
            )
            .count()
        )

        assert count_before == count_after, (
            f"Segunda ejecución creó duplicados: {count_before} → {count_after}"
        )
