from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    db_url: str = "sqlite:///mlmonitor_dev.db"

    # AWS (región compartida para Bedrock, S3 y SES)
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

    # S3 — vacío = upload deshabilitado
    s3_bucket: str = "ml-monitoring-reports-credito"
    s3_prefix: str = "mlmonitor/reports"

    # SES
    ses_from_email: str = ""
    email_from: str = "MLMonitor <noreply@mlmonitor.local>"
    email_recipients: str = ""

    # Artifacts
    artifacts_dir: str = "artifacts"

    @property
    def reports_dir(self) -> Path:
        return Path(self.artifacts_dir) / "reports"

    @property
    def recipient_list(self) -> list[str]:
        return [r.strip() for r in self.email_recipients.split(",") if r.strip()]


def _build_settings() -> Settings:
    """
    Crea el objeto Settings combinando .env (no-sensible) + Secrets Manager (sensible).
    Si SM no está disponible, se usan los defaults (permite dev local sin AWS).
    """
    s = Settings()
    try:
        from config.secrets_loader import load_all_secrets
        overrides = load_all_secrets(s.aws_region)
        return s.model_copy(update=overrides)
    except Exception as e:
        print(f"[settings] Secrets Manager no disponible, usando defaults: {e}")
        return s


settings = _build_settings()
