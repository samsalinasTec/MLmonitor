"""
run_bootstrap_v2.py — Bootstrap experimental con baseline desde variables_serc.

Igual que `run_bootstrap.py` pero usa `ModelBootstrapV2`: el baseline para
`META_BASELINE_DISTRIBUTIONS` se calcula desde las primeras N semanas ISO
del año indicado dentro de `variables_serc_*.csv` (LONG), en lugar de
`base_train_test_bb.csv` (WIDE).

Defaults: year=2026, n_weeks=4.

Uso:
    cd mlmonitor
    rm mlmonitor_dev.db
    poetry run python scripts/run_bootstrap_v2.py
    poetry run python scripts/run_bootstrap_v2.py --year 2026 --n-weeks 4
    poetry run python scripts/run_bootstrap_v2.py --variables-serc-file variables_serc_S262025_S162026.csv
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


def main():
    parser = argparse.ArgumentParser(
        description="Bootstrap V2: baseline desde primeras N semanas de variables_serc"
    )
    parser.add_argument("--db-url", default=None, help="URL de la base de datos")
    parser.add_argument(
        "--raw-dir", default=None,
        help="Directorio con CSVs raw (default: data/inputs/raw_tables)",
    )
    parser.add_argument(
        "--variables-serc-file", default=None,
        help="Nombre del CSV variables_serc (default: auto-detect variables_serc_*.csv en raw-dir)",
    )
    parser.add_argument("--year", type=int, default=2026, help="Año ISO del baseline (default: 2026)")
    parser.add_argument("--n-weeks", type=int, default=4, help="Número de semanas iniciales (default: 4)")
    args = parser.parse_args()

    if args.db_url:
        db_url = args.db_url
    else:
        from config.settings import settings
        db_url = settings.db_url

    project_root = Path(__file__).parent.parent
    raw_dir = Path(args.raw_dir) if args.raw_dir else project_root / "data" / "inputs" / "raw_tables"

    print(f"[bootstrap_v2] DB: {db_url}")
    print(f"[bootstrap_v2] Raw dir: {raw_dir}")
    print(f"[bootstrap_v2] Baseline window: year={args.year} weeks=1..{args.n_weeks}")

    engine = create_db_engine(db_url)

    print("[bootstrap_v2] Creando tablas...")
    Base.metadata.create_all(engine)

    from mlmonitor.data.bootstrap_v2 import ModelBootstrapV2

    with get_session(engine) as session:
        bootstrap = ModelBootstrapV2(
            session,
            raw_dir=raw_dir,
            variables_serc_filename=args.variables_serc_file,
            baseline_year=args.year,
            baseline_n_weeks=args.n_weeks,
        )
        counts = bootstrap.run()

    print("\n[bootstrap_v2] Filas insertadas:")
    for table, count in counts.items():
        print(f"  {table:<35} {count:>6} filas")

    print("\n[bootstrap_v2] Verificacion en DB:")
    from sqlalchemy import text
    with engine.connect() as conn:
        for table_name in [
            "META_MODEL_REGISTRY",
            "META_VARIABLES",
            "META_METRIC_THRESHOLDS",
            "META_BASELINE_DISTRIBUTIONS",
        ]:
            try:
                result = conn.execute(text(f'SELECT COUNT(*) FROM "{table_name}"'))
                count = result.scalar()
                print(f"  {table_name:<35} {count:>6} filas")
            except Exception as e:
                print(f"  {table_name:<35} ERROR: {e}")

    print("\n[bootstrap_v2] Bootstrap V2 completado.")


if __name__ == "__main__":
    main()
