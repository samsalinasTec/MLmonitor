"""
secrets_loader.py — Carga credenciales desde AWS Secrets Manager.

Secretos esperados:
  ml-monitoring/rds    → {username, password, host, port, dbname}
  ml-monitoring/config → {sender_email, recipient_email}
"""

import json


def _fetch_secret(secret_name: str, region: str) -> dict:
    """Llama a Secrets Manager y retorna el secreto como dict."""
    import boto3
    client = boto3.client("secretsmanager", region_name=region)
    response = client.get_secret_value(SecretId=secret_name)
    return json.loads(response["SecretString"])


def load_all_secrets(region: str) -> dict:
    """
    Carga todos los secretos de MLMonitor y los retorna como dict de settings.

    Returns:
        {
            "db_url": "postgresql://user:pass@host:port/dbname",
            "ses_from_email": "sender@domain.com",
            "email_from": "sender@domain.com",
            "email_recipients": "recipient@domain.com",
        }
    """
    # --- RDS credentials ---
    rds = _fetch_secret("ml-monitoring/rds", region)
    db_url = (
        f"postgresql://{rds['username']}:{rds['password']}"
        f"@{rds['host']}:{rds['port']}/{rds['dbname']}"
    )

    # --- App config ---
    config = _fetch_secret("ml-monitoring/SES", region)

    return {
        "db_url": db_url,
        "ses_from_email": config["sender_email"],
        "email_from": config["sender_email"],
        "email_recipients": config["recipient_email"],
    }
