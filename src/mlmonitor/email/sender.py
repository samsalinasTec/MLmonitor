"""
SESEmailSender — Envía el reporte PDF via AWS SES.
"""

from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


class SESEmailSender:
    """Envía correos con adjuntos PDF via AWS SES (send_raw_email)."""

    def __init__(self, from_email: str, region: str = "us-east-1"):
        self.from_email = from_email
        self.region = region
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("ses", region_name=self.region)
        return self._client

    def send_report(
        self,
        recipients: list[str],
        pdf_path: Path,
        subject: str | None = None,
        body: str | None = None,
    ) -> bool:
        """
        Envía el reporte PDF a la lista de destinatarios via SES.

        Args:
            recipients: Lista de emails
            pdf_path: Ruta al archivo PDF
            subject: Asunto del correo (default auto-generado)
            body: Cuerpo HTML (default auto-generado)

        Returns:
            True si el envío fue exitoso
        """
        if not recipients:
            print("[SESEmailSender] No hay destinatarios configurados.")
            return False

        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF no encontrado: {pdf_path}")

        if subject is None:
            subject = f"MLMonitor — Reporte de Monitoreo {pdf_path.stem}"

        if body is None:
            body = self._default_body(pdf_path.name)

        msg = MIMEMultipart()
        msg["From"] = self.from_email
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject

        msg.attach(MIMEText(body, "html", "utf-8"))

        with open(pdf_path, "rb") as f:
            pdf_attachment = MIMEApplication(f.read(), _subtype="pdf")
            pdf_attachment.add_header(
                "Content-Disposition",
                "attachment",
                filename=pdf_path.name,
            )
            msg.attach(pdf_attachment)

        self._get_client().send_raw_email(
            Source=self.from_email,
            Destinations=recipients,
            RawMessage={"Data": msg.as_string()},
        )

        print(
            f"[SESEmailSender] Reporte enviado a {len(recipients)} destinatarios: "
            f"{', '.join(recipients)}"
        )
        return True

    def _default_body(self, filename: str) -> str:
        return f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <h2 style="color: #0f3460;">MLMonitor — Reporte de Monitoreo de Scorecards</h2>
            <p>Estimado equipo,</p>
            <p>Se adjunta el reporte automático de monitoreo de la flota de scorecards de crédito (BazBoost).</p>
            <p>Por favor revisar las alertas activas, especialmente los segmentos con estado <strong>CRÍTICO</strong> o <strong>WARNING</strong>.</p>
            <hr style="border: 1px solid #eee;">
            <p style="font-size: 11px; color: #999;">
                Este correo fue generado automáticamente por <strong>MLMonitor</strong>.<br>
                Archivo adjunto: <code>{filename}</code>
            </p>
        </body>
        </html>
        """

    @classmethod
    def from_settings(cls) -> "SESEmailSender":
        from config.settings import settings
        return cls(
            from_email=settings.ses_from_email or settings.email_from,
            region=settings.aws_region,
        )
