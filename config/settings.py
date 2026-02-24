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

    # LLM
    llm_provider: str = "vertex"

    # Google Cloud (Vertex AI / Gemini)
    google_cloud_project: str = ""
    google_cloud_location: str = "us-central1"
    google_cloud_model: str = "gemini-2.5-flash"

    # AWS Bedrock
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "anthropic.claude-3-sonnet-20240229-v1:0"

    # Email
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_recipients: str = ""
    email_from: str = "MLMonitor <noreply@mlmonitor.local>"

    # Artifacts
    artifacts_dir: str = "artifacts"

    @property
    def reports_dir(self) -> Path:
        return Path(self.artifacts_dir) / "reports"

    @property
    def recipient_list(self) -> list[str]:
        return [r.strip() for r in self.email_recipients.split(",") if r.strip()]


settings = Settings()
