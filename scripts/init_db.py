"""
init_db.py — Crea tablas y carga dummy data en la base de datos.

Uso:
    cd mlmonitor
    python scripts/init_db.py
    python scripts/init_db.py --db-url sqlite:///mlmonitor_dev.db
"""

import sys
from pathlib import Path

# Agregar src al path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse

from mlmonitor.db.connection import create_db_engine
from mlmonitor.db.models import Base
from mlmonitor.db.session import get_session
from mlmonitor.data.dummy_generator import DummyDataGenerator


def init_db(db_url: str) -> None:
    print(f"[init_db] Conectando a: {db_url}")
    engine = create_db_engine(db_url)

    print("[init_db] Creando tablas...")
    Base.metadata.create_all(engine)
    print("[init_db] Tablas creadas OK")

    print("[init_db] Generando dummy data...")
    with get_session(engine) as session:
        generator = DummyDataGenerator(session)
        counts = generator.run()

    print("\n[init_db] Filas insertadas por tabla:")
    for table, count in counts.items():
        print(f"  {table:<35} {count:>6} filas")

    # Verificación: leer counts de la DB
    print("\n[init_db] Verificación en DB:")
    from sqlalchemy import text
    with engine.connect() as conn:
        for table_name in [
            "META_MODEL_REGISTRY",
            "META_VARIABLES",
            "META_METRIC_THRESHOLDS",
            "FACT_DISTRIBUTIONS",
            "FACT_PERFORMANCE_OUTCOMES",
            "FACT_METRICS_HISTORY",
        ]:
            result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
            count = result.scalar()
            print(f"  {table_name:<35} {count:>6} filas")

    print("\n[init_db] ¡Base de datos inicializada exitosamente!")


def main():
    parser = argparse.ArgumentParser(description="Inicializa la base de datos MLMonitor")
    parser.add_argument(
        "--db-url",
        default=None,
        help="URL de la base de datos (default: settings.db_url)",
    )
    args = parser.parse_args()

    if args.db_url:
        db_url = args.db_url
    else:
        from config.settings import settings
        db_url = settings.db_url

    init_db(db_url)


if __name__ == "__main__":
    main()
