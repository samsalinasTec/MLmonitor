"""
SessionFactory con context manager para manejo seguro de transacciones.
"""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker


def get_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


@contextmanager
def get_session(engine: Engine) -> Generator[Session, None, None]:
    """Context manager que provee una sesión con commit/rollback automático."""
    SessionLocal = get_session_factory(engine)
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
