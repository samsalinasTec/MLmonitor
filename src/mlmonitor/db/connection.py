"""
Engine factory: SQLite para desarrollo, Oracle para producción.
Detecta el dialecto por el prefijo de DB_URL.
"""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def create_db_engine(db_url: str, **kwargs) -> Engine:
    """
    Crea un engine SQLAlchemy apropiado según el dialecto.

    - sqlite:// → SQLite (dev/testing)
    - oracle+cx_oracle:// o oracle+oracledb:// → Oracle (prod)
    """
    connect_args = {}

    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        engine = create_engine(
            db_url,
            connect_args=connect_args,
            echo=kwargs.get("echo", False),
        )
    elif "oracle" in db_url:
        engine = create_engine(
            db_url,
            echo=kwargs.get("echo", False),
            pool_size=kwargs.get("pool_size", 5),
            max_overflow=kwargs.get("max_overflow", 10),
        )
    else:
        # Fallback genérico (PostgreSQL, MySQL, etc.)
        engine = create_engine(db_url, echo=kwargs.get("echo", False))

    return engine


def get_engine(db_url: str | None = None) -> Engine:
    """Obtiene el engine usando la config global si no se provee URL."""
    if db_url is None:
        from config.settings import settings
        db_url = settings.db_url
    return create_db_engine(db_url)
