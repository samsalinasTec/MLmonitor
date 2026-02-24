"""
EmailSender — Envía el reporte PDF por SMTP con starttls.
"""

import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


class EmailSender:
    """Envía correos con adjuntos PDF via SMTP estándar."""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: str,
        smtp_password: str,
        email_from: str,
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.email_from = email_from

    def send_report(
        self,
        recipients: list[str],
        pdf_path: Path,
        subject: str | None = None,
        body: str | None = None,
    ) -> bool:
        """
        Envía el reporte PDF a la lista de destinatarios.

        Args:
            recipients: Lista de emails
            pdf_path: Ruta al archivo PDF
            subject: Asunto del correo (default auto-generado)
            body: Cuerpo del correo (default auto-generado)

        Returns:
            True si el envío fue exitoso
        """
        if not recipients:
            print("[EmailSender] No hay destinatarios configurados.")
            return False

        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF no encontrado: {pdf_path}")

        if subject is None:
            subject = f"MLMonitor — Reporte de Monitoreo {pdf_path.stem}"

        if body is None:
            body = self._default_body(pdf_path.name)

        msg = MIMEMultipart()
        msg["From"] = self.email_from
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject

        msg.attach(MIMEText(body, "html", "utf-8"))

        # Adjuntar PDF
        with open(pdf_path, "rb") as f:
            pdf_attachment = MIMEApplication(f.read(), _subtype="pdf")
            pdf_attachment.add_header(
                "Content-Disposition",
                "attachment",
                filename=pdf_path.name,
            )
            msg.attach(pdf_attachment)

        # Envío SMTP con starttls
        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
            server.sendmail(self.email_from, recipients, msg.as_string())

        print(
            f"[EmailSender] Reporte enviado a {len(recipients)} destinatarios: "
            f"{', '.join(recipients)}"
        )
        return True

    def _default_body(self, filename: str) -> str:
        return f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <h2 style="color: #0f3460;">MLMonitor — Reporte de Monitoreo de Scorecards</h2>
            <p>Estimado equipo,</p>
            <p>Se adjunta el reporte automático de monitoreo de la flota de scorecards de crédito y cobranza.</p>
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
    def from_settings(cls) -> "EmailSender":
        """Crea un EmailSender desde la configuración global."""
        from config.settings import settings
        return cls(
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_user=settings.smtp_user,
            smtp_password=settings.smtp_password,
            email_from=settings.email_from,
        )
