"""
export_rds_samples.py — Extrae 150 filas de cada tabla del modelo de datos desde RDS y las guarda como CSV.

Uso:
    cd mlmonitor
    poetry run python scripts/export_rds_samples.py
    poetry run python scripts/export_rds_samples.py --db-url postgresql://user:pass@host/db
    poetry run python scripts/export_rds_samples.py --limit 200
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse

import pandas as pd
from sqlalchemy import create_engine, text

TABLES = [
    "META_MODEL_REGISTRY",
    "META_VARIABLES",
    "META_METRIC_THRESHOLDS",
    "FACT_DISTRIBUTIONS",
    "FACT_PERFORMANCE_OUTCOMES",
    "FACT_METRICS_HISTORY",
]


def export_samples(db_url: str, limit: int, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(db_url)

    print(f"Conectado a: {db_url.split('@')[-1] if '@' in db_url else db_url}")
    print(f"Exportando {limit} filas por tabla → {output_dir}\n")

    with engine.connect() as conn:
        for table in TABLES:
            # Detectar dialect para usar LIMIT o TOP
            is_pg = db_url.startswith("postgresql")
            if is_pg:
                query = text(f'SELECT * FROM "{table}" LIMIT :lim')
                df = pd.read_sql(query, conn, params={"lim": limit})
            else:
                # SQLite no necesita comillas dobles en mayúsculas si el nombre es case-insensitive
                query = text(f'SELECT * FROM "{table}" LIMIT :lim')
                df = pd.read_sql(query, conn, params={"lim": limit})

            out_path = output_dir / f"{table}.csv"
            df.to_csv(out_path, index=False)

            print(f"  {table:<35} {df.shape[0]:>5} filas × {df.shape[1]:>2} cols  →  {out_path.name}")

    print("\n¡Exportación completada!")


def main():
    parser = argparse.ArgumentParser(description="Exporta samples de RDS a CSV")
    parser.add_argument("--db-url", default=None, help="URL de la DB (default: settings.db_url)")
    parser.add_argument("--limit", type=int, default=150, help="Filas a exportar por tabla (default: 150)")
    args = parser.parse_args()

    if args.db_url:
        db_url = args.db_url
    else:
        from config.settings import settings
        db_url = settings.db_url

    project_root = Path(__file__).parent.parent
    output_dir = project_root / "data" / "Transform"

    export_samples(db_url, args.limit, output_dir)


if __name__ == "__main__":
    main()
