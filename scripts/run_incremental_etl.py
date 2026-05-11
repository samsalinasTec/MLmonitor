"""
run_incremental_etl.py — Ejecuta ETL incremental para una semana dada.

Carga datos de CSVs, filtra por semana, y ejecuta los dos flujos:
  Flow A: distribuciones (PSI)
  Flow B: performance (cohortes maduras)

Se corre ANTES del pipeline (run_pipeline.py) para cada semana de datos.

Uso:
    cd mlmonitor
    # Auto-detect semana desde el filename (S{WW}{YYYY}_S{WW}{YYYY}):
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
import re
from datetime import date

import pandas as pd

from mlmonitor.db.connection import create_db_engine
from mlmonitor.db.session import get_session

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")


def parse_week_range(filename: str) -> tuple[date, date]:
    """Extrae rango de semanas ISO del nombre de archivo.

    Nomenclatura: *_S{WW}{YYYY}_S{WW}{YYYY}.csv
    Retorna (lunes_inicio, lunes_fin).
    """
    m = re.search(r"_S(\d{1,2})(\d{4})_S(\d{1,2})(\d{4})", filename)
    if not m:
        raise ValueError(f"No se pudo parsear rango de semanas de: {filename}")
    w_start, y_start = int(m[1]), int(m[2])
    w_end, y_end = int(m[3]), int(m[4])
    return (
        date.fromisocalendar(y_start, w_start, 1),
        date.fromisocalendar(y_end, w_end, 1),
    )


def _resolve_csv(raw_dir: Path, prefix: str, explicit: str | None) -> tuple[Path, date | None]:
    """Resuelve CSV por nombre explícito o glob. Extrae fecha fin del rango."""
    if explicit:
        path = raw_dir / explicit
        try:
            _, end = parse_week_range(path.name)
            return path, end
        except ValueError:
            return path, None

    candidates = list(raw_dir.glob(f"{prefix}_S*_S*.csv"))
    if not candidates:
        return raw_dir / f"{prefix}.csv", None
    if len(candidates) > 1:
        logging.warning("Multiple %s files found: %s", prefix, [c.name for c in candidates])
    path = candidates[-1]
    _, end = parse_week_range(path.name)
    return path, end


def main():
    parser = argparse.ArgumentParser(description="ETL incremental semanal")
    parser.add_argument(
        "--date", required=False, default=None,
        help="Semana de ejecucion (YYYY-MM-DD). Si se omite, se deriva del rango en el filename (S{WW}{YYYY}).",
    )
    parser.add_argument("--db-url", default=None, help="URL de la base de datos")
    parser.add_argument(
        "--raw-dir", default=None,
        help="Directorio con CSVs raw (default: data/inputs/raw_tables)",
    )
    parser.add_argument(
        "--model-id", default=None,
        help=(
            "Model ID a procesar. Si se omite, se ejecuta el ETL para todos "
            "los modelos activos de META_MODEL_REGISTRY (uno por uno)."
        ),
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

    if args.db_url:
        db_url = args.db_url
    else:
        from config.settings import settings
        db_url = settings.db_url

    project_root = Path(__file__).parent.parent
    raw_dir = Path(args.raw_dir) if args.raw_dir else project_root / "data" / "inputs" / "raw_tables"

    # Resolve CSV paths and extract date ranges from filenames
    serc_path, serc_end = _resolve_csv(raw_dir, "variables_serc", args.serc_file)
    weekly_path, weekly_end = _resolve_csv(raw_dir, "muestra_weekly", args.weekly_file)

    # execution_week: explicit --date > filename end-date > auto-detect from DF
    file_end_date = max(filter(None, [serc_end, weekly_end]), default=None)

    if args.date:
        execution_week = date.fromisoformat(args.date)
    elif file_end_date:
        execution_week = file_end_date
    else:
        execution_week = None

    source = (
        f"--date {args.date}" if args.date
        else f"filename ({file_end_date})" if file_end_date
        else "auto-detect from semana_observacion"
    )
    print(f"[etl] Execution week: {execution_week or 'pending'} (source: {source})")
    print(f"[etl] DB: {db_url}")
    print(f"[etl] Raw dir: {raw_dir}")

    engine = create_db_engine(db_url)

    variables_df = None
    weekly_df = None

    if serc_path.exists():
        print(f"[etl] Loading {serc_path.name}...")
        try:
            start, end = parse_week_range(serc_path.name)
            print(f"[etl]   rango: S{start.isocalendar().week}/{start.year} → S{end.isocalendar().week}/{end.year}  ({start} → {end})")
        except ValueError:
            pass
        variables_df = pd.read_csv(serc_path)
    else:
        print(f"[etl] SERC file not found: {serc_path}")

    if weekly_path.exists():
        print(f"[etl] Loading {weekly_path.name}...")
        try:
            start, end = parse_week_range(weekly_path.name)
            print(f"[etl]   rango: S{start.isocalendar().week}/{start.year} → S{end.isocalendar().week}/{end.year}  ({start} → {end})")
        except ValueError:
            pass
        weekly_df = pd.read_csv(weekly_path)
    else:
        print(f"[etl] Weekly file not found: {weekly_path}")

    from mlmonitor.data.incremental_etl import IncrementalETL
    from mlmonitor.data.model_config import ModelConfig
    from mlmonitor.data.model_registry import resolve_model_ids

    # Resolver lista de modelos a procesar
    with get_session(engine) as session:
        model_ids = resolve_model_ids(session, args.model_id)
    print(f"[etl] Modelos a procesar: {model_ids}")

    actual_week = execution_week or (
        IncrementalETL.detect_execution_week(weekly_df) if weekly_df is not None else "unknown"
    )

    failed: list[tuple[str, str]] = []
    for model_id in model_ids:
        print(f"\n[etl] === Modelo: {model_id} ===")
        try:
            config = ModelConfig.for_model(model_id)
            with get_session(engine) as session:
                etl = IncrementalETL(session, config=config)
                result = etl.run(execution_week, variables_df, weekly_df)
            print(f"[etl] Resultados para semana {actual_week}:")
            for key, value in result.items():
                print(f"  {key:<35} {value:>6}")
        except Exception as e:
            print(f"[etl] ✗ Modelo {model_id} falló: {e}")
            failed.append((model_id, str(e)))

    if failed:
        print(f"\n[etl] {len(failed)}/{len(model_ids)} modelos fallaron:")
        for mid, err in failed:
            print(f"  ✗ {mid}: {err}")
        sys.exit(1)

    print("\n[etl] ETL incremental completado.")


if __name__ == "__main__":
    main()
