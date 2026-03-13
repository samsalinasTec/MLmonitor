"""
init_db.py — Crea tablas y carga data en la base de datos.

Uso:
    cd mlmonitor
    python scripts/init_db.py                          # dummy data (default)
    python scripts/init_db.py --source dummy            # dummy data
    python scripts/init_db.py --source real             # real raw data
    python scripts/init_db.py --db-url sqlite:///mlmonitor_dev.db
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import logging

from mlmonitor.db.connection import create_db_engine
from mlmonitor.db.models import Base
from mlmonitor.db.session import get_session

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")


def init_db(db_url: str, source: str = "dummy") -> None:
    print(f"[init_db] Conectando a: {db_url}")
    engine = create_db_engine(db_url)

    print("[init_db] Creando tablas...")
    Base.metadata.create_all(engine)
    print("[init_db] Tablas creadas OK")

    if source == "dummy":
        print("[init_db] Generando dummy data...")
        from mlmonitor.data.dummy_generator import DummyDataGenerator

        with get_session(engine) as session:
            generator = DummyDataGenerator(session)
            counts = generator.run()
    elif source == "real":
        print("[init_db] Cargando data real desde raw tables...")
        from mlmonitor.data.raw_etl import RawDataETL

        project_root = Path(__file__).parent.parent
        raw_dir = project_root / "data" / "inputs" / "raw_tables"
        transform_dir = project_root / "data" / "Transform"

        with get_session(engine) as session:
            etl = RawDataETL(session, raw_dir=raw_dir, transform_dir=transform_dir)
            counts = etl.run()
    else:
        raise ValueError(f"source debe ser 'dummy' o 'real', recibido: {source}")

    print("\n[init_db] Filas insertadas por tabla:")
    for table, count in counts.items():
        print(f"  {table:<35} {count:>6} filas")

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
            result = conn.execute(text(f'SELECT COUNT(*) FROM "{table_name}"'))
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
    parser.add_argument(
        "--source",
        choices=["dummy", "real"],
        default="dummy",
        help="Fuente de datos: 'dummy' para data sintética, 'real' para raw tables (default: dummy)",
    )
    args = parser.parse_args()

    if args.db_url:
        db_url = args.db_url
    else:
        from config.settings import settings
        db_url = settings.db_url

    init_db(db_url, source=args.source)


if __name__ == "__main__":
    main()
