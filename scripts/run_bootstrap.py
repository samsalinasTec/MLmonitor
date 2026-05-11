"""
run_bootstrap.py — Inicializa DB y ejecuta bootstrap de META + distribuciones baseline.

Se corre UNA vez por modelo. Crea tablas y pobla:
- META_MODEL_REGISTRY (11 segmentos)
- META_VARIABLES (input + output + target por segmento, con binning_rules)
- META_METRIC_THRESHOLDS (umbrales globales)
- META_BASELINE_DISTRIBUTIONS (distribuciones del baseline de entrenamiento)

Uso:
    cd mlmonitor
    poetry run python scripts/run_bootstrap.py
    poetry run python scripts/run_bootstrap.py --db-url sqlite:///mlmonitor_dev.db
    poetry run python scripts/run_bootstrap.py --baseline-file base_train_test_bb.csv
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
    parser = argparse.ArgumentParser(description="Bootstrap: inicializa META tables y distribuciones baseline")
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
        "--baseline-file", default=None,
        help="Nombre del CSV del baseline de entrenamiento (default: auto-detect base_train_test_bb*.csv en raw-dir)",
    )
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

    engine = create_db_engine(db_url)

    print("[bootstrap] Creando tablas...")
    Base.metadata.create_all(engine)

    from mlmonitor.data.bootstrap import ModelBootstrap

    with get_session(engine) as session:
        bootstrap = ModelBootstrap(
            session,
            config=config,
            raw_dir=raw_dir,
            baseline_filename=args.baseline_file,
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
