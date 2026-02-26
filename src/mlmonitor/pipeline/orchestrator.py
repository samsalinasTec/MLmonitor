"""
PipelineOrchestrator — Ejecuta los pasos del pipeline completo.

Step 1: MetricsCalculator.run_for_model()  → popula FACT_METRICS_HISTORY
Step 2: ReportBuilder.build()              → consulta DB + llama LLM (Bedrock)
Step 3: PDFRenderer.render_pdf()           → genera archivo PDF
Step 3b: S3Uploader.upload()               → sube PDF a S3 (si S3_BUCKET configurado)
Step 4: SESEmailSender.send_report()       → envía correo via SES
"""

from datetime import date
from pathlib import Path

from sqlalchemy.engine import Engine

from mlmonitor.analyst.base import AnalysisContext, AnalysisResult
from mlmonitor.db.models import Base
from mlmonitor.db.session import get_session
from mlmonitor.email.sender import SESEmailSender
from mlmonitor.metrics.calculator import MetricsCalculator
from mlmonitor.report.builder import ReportBuilder
from mlmonitor.report.renderer import PDFRenderer

MODEL_ID = "BAZBOOST_V1"


class PipelineOrchestrator:
    """Orquesta el pipeline completo de monitoreo."""

    def __init__(
        self,
        engine: Engine,
        output_dir: str | Path | None = None,
    ):
        self.engine = engine
        self.output_dir = Path(output_dir) if output_dir else Path("artifacts/reports")

    def run(
        self,
        model_id: str = MODEL_ID,
        calculation_date: date | None = None,
        send_email: bool = True,
        analyst=None,
    ) -> dict:
        """
        Ejecuta el pipeline completo.

        Args:
            model_id: ID del modelo a monitorear
            calculation_date: fecha de cálculo (default: hoy)
            send_email: si True, envía el PDF por correo
            analyst: instancia de BaseAnalyst (None = sin LLM)

        Returns:
            dict con resultados de cada paso
        """
        if calculation_date is None:
            calculation_date = date.today()

        results = {
            "model_id": model_id,
            "calculation_date": calculation_date.isoformat(),
            "steps": {},
        }

        print(f"\n{'='*60}")
        print(f"MLMonitor Pipeline — {model_id}")
        print(f"Semana de cálculo: {calculation_date}")
        print(f"{'='*60}")

        # ----------------------------------------------------------------
        # Step 1: Calcular métricas
        # ----------------------------------------------------------------
        print("\n[Step 1] Calculando métricas...")
        with get_session(self.engine) as session:
            calculator = MetricsCalculator(session)
            metrics_rows = calculator.run_for_model(model_id, calculation_date)

        results["steps"]["metrics"] = {
            "status": "ok",
            "rows_inserted": len(metrics_rows),
        }
        print(f"         ✓ {len(metrics_rows)} métricas calculadas y guardadas")

        # ----------------------------------------------------------------
        # Step 2: Construir reporte (+ LLM si hay analista)
        # ----------------------------------------------------------------
        print("\n[Step 2] Construyendo reporte...")
        with get_session(self.engine) as session:
            builder = ReportBuilder(session)
            context, llm_result = builder.build(
                model_id=model_id,
                calculation_week=calculation_date,
                analyst=analyst,
            )

        llm_status = "ok (con LLM)" if llm_result else "ok (sin LLM)"
        results["steps"]["report_build"] = {
            "status": llm_status,
            "segments": len(context.segments),
            "fleet_summary": context.fleet_summary,
        }
        print(f"         ✓ Contexto construido: {len(context.segments)} segmentos")
        if llm_result:
            print(f"         ✓ Narrativas LLM generadas")

        # ----------------------------------------------------------------
        # Step 3: Generar PDF
        # ----------------------------------------------------------------
        print("\n[Step 3] Generando PDF...")
        renderer = PDFRenderer(output_dir=self.output_dir)
        pdf_path = renderer.render_pdf(
            context=context,
            result=llm_result,
            generation_date=calculation_date,
            filename=f"mlmonitor_{calculation_date.isoformat()}.pdf",
        )

        results["steps"]["pdf"] = {
            "status": "ok",
            "path": str(pdf_path),
        }
        print(f"         ✓ Reporte guardado: {pdf_path}")

        # ----------------------------------------------------------------
        # Step 3b: Subir PDF a S3 (opcional — solo si S3_BUCKET configurado)
        # ----------------------------------------------------------------
        s3_uri = None
        from config.settings import settings as _s
        if _s.s3_bucket:
            print("\n[Step 3b] Subiendo PDF a S3...")
            try:
                from mlmonitor.storage.s3_uploader import S3Uploader
                s3_uri = S3Uploader.from_settings().upload(pdf_path)
                results["steps"]["s3_upload"] = {"status": "ok", "uri": s3_uri}
            except Exception as e:
                results["steps"]["s3_upload"] = {"status": "error", "error": str(e)}
                print(f"         ✗ Error subiendo a S3: {e}")
        else:
            results["steps"]["s3_upload"] = {"status": "skipped"}

        # ----------------------------------------------------------------
        # Step 4: Enviar por email via SES
        # ----------------------------------------------------------------
        if send_email:
            print("\n[Step 4] Enviando reporte por email (SES)...")
            try:
                email_sender = SESEmailSender.from_settings()
                success = email_sender.send_report(
                    recipients=_s.recipient_list,
                    pdf_path=pdf_path,
                )
                results["steps"]["email"] = {
                    "status": "ok" if success else "no_recipients",
                    "recipients": _s.recipient_list,
                }
                if success:
                    print(f"         ✓ Email enviado a {len(_s.recipient_list)} destinatarios")
            except Exception as e:
                results["steps"]["email"] = {"status": "error", "error": str(e)}
                print(f"         ✗ Error enviando email: {e}")
        else:
            print("\n[Step 4] Email omitido (--no-email)")
            results["steps"]["email"] = {"status": "skipped"}

        # ----------------------------------------------------------------
        # Resumen
        # ----------------------------------------------------------------
        print(f"\n{'='*60}")
        print("Pipeline completado exitosamente.")
        fleet = context.fleet_summary
        print(
            f"Estado de flota: {fleet['total']} segmentos — "
            f"{fleet['ok']} OK | {fleet['warning']} WARNING | {fleet['critical']} CRITICAL"
        )
        print(f"Reporte local: {pdf_path}")
        if s3_uri:
            print(f"Reporte S3:    {s3_uri}")
        print(f"{'='*60}\n")

        results["pdf_path"] = str(pdf_path)
        results["s3_uri"] = s3_uri
        results["fleet_summary"] = context.fleet_summary
        return results
