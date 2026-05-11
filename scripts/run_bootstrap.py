"""
run_bootstrap.py — Inicializa DB y ejecuta bootstrap de META + distribuciones baseline.

Se corre UNA vez por modelo. Crea tablas y pobla:
- META_MODEL_REGISTRY (una fila por segmento del modelo)
- META_VARIABLES (input + output + target por segmento, con binning_rules)
- META_METRIC_THRESHOLDS (umbrales por segmento, desde thresholds.csv del modelo)
- META_BASELINE_DISTRIBUTIONS (distribuciones derivadas de las primeras N
  semanas ISO de `variables_serc_*.csv`; ver ADR §8.2.29)

Histórico: hasta Iteración 2 coexistían `bootstrap.py` (baseline WIDE, legacy)
y `bootstrap_v2.py` (baseline desde variables_serc, oficial). D7 los consolidó
en un solo `ModelBootstrap` con el camino V2 — el WIDE quedó retirado.

Uso:
    cd mlmonitor
    poetry run python scripts/run_bootstrap.py --model-id BAZBOOST_V1
    poetry run python scripts/run_bootstrap.py --model-id BAZBOOST_V1 \\
        --db-url sqlite:///mlmonitor_dev.db
    poetry run python scripts/run_bootstrap.py --model-id BAZBOOST_V1 \\
        --year 2026 --n-weeks 4
    poetry run python scripts/run_bootstrap.py --model-id BAZBOOST_V1 \\
        --variables-serc-file variables_serc_S262025_S162026.csv
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
        description=(
            "Bootstrap: inicializa META tables y distribuciones baseline "
            "(derivadas de las primeras N semanas ISO de variables_serc_*.csv)."
        ),
    )
    parser.add_argument(
        "--model-id", required=True,
        help=(
            "ID del modelo a registrar (ej: BAZBOOST_V1). Va a META_MODEL_REGISTRY.model_id. "
            "El resto de la configuración (primary_target, segments, variables, score_bins, etc.) "
            "se carga de data/inputs/model_configs/<model_id_lowercase>/config.json."
        ),
    )
    parser.add_argument("--db-url", default=None, help="URL de la base de datos")
    parser.add_argument(
        "--raw-dir", default=None,
        help="Directorio con CSVs raw para datos semanales (default: data/inputs/raw_tables)",
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

    from mlmonitor.data.model_config import ModelConfig
    config = ModelConfig.for_model(args.model_id)

    print(f"[bootstrap] DB: {db_url}")
    print(f"[bootstrap] Raw dir: {raw_dir}")
    print(f"[bootstrap] Model ID: {config.model_id}")
    print(f"[bootstrap] Config dir: {config.config_dir}")
    print(f"[bootstrap] Primary target: {config.primary_target}")
    print(f"[bootstrap] Baseline window: year={args.year} weeks=1..{args.n_weeks}")

    engine = create_db_engine(db_url)

    print("[bootstrap] Creando tablas...")
    Base.metadata.create_all(engine)

    from mlmonitor.data.bootstrap import ModelBootstrap

    with get_session(engine) as session:
        bootstrap = ModelBootstrap(
            session,
            config=config,
            raw_dir=raw_dir,
            variables_serc_filename=args.variables_serc_file,
            baseline_year=args.year,
            baseline_n_weeks=args.n_weeks,
        )
        counts = bootstrap.run()

    print("\n[bootstrap] Filas insertadas:")
    for table, count in counts.items():
        print(f"  {table:<35} {count:>6} filas")

    print("\n[bootstrap] Verificacion en DB:")
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

    print("\n[bootstrap] Bootstrap completado.")


if __name__ == "__main__":
    main()
