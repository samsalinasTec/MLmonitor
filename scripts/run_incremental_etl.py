"""
run_incremental_etl.py — Ejecuta ETL incremental para una semana dada.

Carga datos de CSVs, filtra por semana, y ejecuta los dos flujos:
  Flow A: distribuciones (PSI)
  Flow B: performance (cohortes maduras)

Se corre ANTES del pipeline (run_pipeline.py) para cada semana de datos.

Uso:
    cd mlmonitor
    # Auto-detect semana desde MAX(semana_observacion) en muestra_weekly:
    poetry run python scripts/run_incremental_etl.py
    # Semana manual (backfill o testing):
    poetry run python scripts/run_incremental_etl.py --date 2025-08-19
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import logging
from datetime import date

import pandas as pd

from mlmonitor.db.connection import create_db_engine
from mlmonitor.db.session import get_session

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")


def main():
    parser = argparse.ArgumentParser(description="ETL incremental semanal")
    parser.add_argument(
        "--date", required=False, default=None,
        help="Semana de ejecucion (YYYY-MM-DD). Si se omite, se auto-detecta desde MAX(semana_observacion) en muestra_weekly.",
    )
    parser.add_argument("--db-url", default=None, help="URL de la base de datos")
    parser.add_argument(
        "--raw-dir", default=None,
        help="Directorio con CSVs raw (default: data/inputs/raw_tables)",
    )
    parser.add_argument(
        "--model-id", default="BAZBOOST_V1",
        help="Model ID (default: BAZBOOST_V1)",
    )
    parser.add_argument(
        "--serc-file", default=None,
        help="Nombre del CSV de variables SERC (default: auto-detect variables_serc_*.csv en raw-dir)",
    )
    parser.add_argument(
        "--weekly-file", default=None,
        help="Nombre del CSV de muestra weekly (default: auto-detect muestra_weekly_*.csv en raw-dir)",
    )
    args = parser.parse_args()

    execution_week = date.fromisoformat(args.date) if args.date else None

    if args.db_url:
        db_url = args.db_url
    else:
        from config.settings import settings
        db_url = settings.db_url

    project_root = Path(__file__).parent.parent
    raw_dir = Path(args.raw_dir) if args.raw_dir else project_root / "data" / "inputs" / "raw_tables"

    print(f"[etl] Execution week: {execution_week or 'auto-detect from semana_observacion'}")
    print(f"[etl] DB: {db_url}")
    print(f"[etl] Raw dir: {raw_dir}")

    engine = create_db_engine(db_url)

    # Resolve CSV paths: explicit > glob > fallback
    if args.serc_file:
        serc_path = raw_dir / args.serc_file
    else:
        serc_candidates = sorted(raw_dir.glob("variables_serc_*.csv"))
        serc_path = serc_candidates[0] if serc_candidates else raw_dir / "variables_serc.csv"

    if args.weekly_file:
        weekly_path = raw_dir / args.weekly_file
    else:
        weekly_candidates = sorted(raw_dir.glob("muestra_weekly_*.csv"))
        weekly_path = weekly_candidates[0] if weekly_candidates else raw_dir / "muestra_weekly.csv"

    variables_df = None
    weekly_df = None

    if serc_path.exists():
        print(f"[etl] Loading {serc_path.name}...")
        variables_df = pd.read_csv(serc_path)
    else:
        print(f"[etl] SERC file not found: {serc_path}")

    if weekly_path.exists():
        print(f"[etl] Loading {weekly_path.name}...")
        weekly_df = pd.read_csv(weekly_path)
    else:
        print(f"[etl] Weekly file not found: {weekly_path}")

    from mlmonitor.data.incremental_etl import IncrementalETL

    with get_session(engine) as session:
        etl = IncrementalETL(session, model_id=args.model_id)
        result = etl.run(execution_week, variables_df, weekly_df)

    # Show the actual execution week used (may have been auto-detected)
    actual_week = execution_week or (
        IncrementalETL.detect_execution_week(weekly_df) if weekly_df is not None else "unknown"
    )
    print(f"\n[etl] Resultados para semana {actual_week}:")
    for key, value in result.items():
        print(f"  {key:<35} {value:>6}")

    print("\n[etl] ETL incremental completado.")


if __name__ == "__main__":
    main()
