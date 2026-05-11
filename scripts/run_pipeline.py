"""
run_pipeline.py — Entry point del pipeline completo de MLMonitor.

Uso:
    cd mlmonitor
    python scripts/run_pipeline.py
    python scripts/run_pipeline.py --date 2026-02-10
    python scripts/run_pipeline.py --no-email
    python scripts/run_pipeline.py --no-llm --no-email
"""

import sys
from pathlib import Path

# Agregar src y config al path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
from datetime import date


def parse_args():
    parser = argparse.ArgumentParser(
        description="Ejecuta el pipeline completo de MLMonitor"
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Fecha de cálculo ISO (YYYY-MM-DD). Default: hoy",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="URL de la base de datos. Default: settings.db_url",
    )
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="No enviar email (útil para debug)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="No llamar al LLM (genera reporte sin narrativas)",
    )
    parser.add_argument(
        "--model-id",
        type=str,
        default=None,
        help=(
            "ID del modelo a monitorear. Si se omite, se procesan todos los "
            "modelos activos de META_MODEL_REGISTRY (uno por uno)."
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Fecha de cálculo
    if args.date:
        calculation_date = date.fromisoformat(args.date)
    else:
        calculation_date = None  # orchestrator auto-detecta desde DB

    # Configuración
    from config.settings import settings
    db_url = args.db_url or settings.db_url
    output_dir = settings.reports_dir

    print(f"[run_pipeline] DB: {db_url}")
    print(f"[run_pipeline] Fecha: {calculation_date or 'auto-detect desde DB'}")
    print(f"[run_pipeline] LLM: {'desactivado' if args.no_llm else 'Bedrock'}")

    # Crear engine
    from mlmonitor.db.connection import create_db_engine
    engine = create_db_engine(db_url)

    # Resolver lista de modelos a procesar
    from mlmonitor.data.model_registry import resolve_model_ids
    from mlmonitor.db.session import get_session
    with get_session(engine) as session:
        model_ids = resolve_model_ids(session, args.model_id)
    print(f"[run_pipeline] Modelos a procesar: {model_ids}")

    # Crear analista LLM si se requiere (compartido entre todos los modelos)
    analyst = None
    if not args.no_llm:
        try:
            from mlmonitor.analyst import create_analyst
            analyst = create_analyst()
            print(f"[run_pipeline] Analista LLM: {type(analyst).__name__}")
        except Exception as e:
            print(f"[run_pipeline] Advertencia: no se pudo inicializar el LLM: {e}")
            print("[run_pipeline] Continuando sin narrativas LLM...")

    # Ejecutar pipeline para cada modelo activo
    from mlmonitor.pipeline.orchestrator import PipelineOrchestrator
    orchestrator = PipelineOrchestrator(engine=engine, output_dir=output_dir)

    failed: list[tuple[str, str]] = []
    for model_id in model_ids:
        print(f"\n[run_pipeline] === Modelo: {model_id} ===")
        try:
            orchestrator.run(
                model_id=model_id,
                calculation_date=calculation_date,
                send_email=not args.no_email,
                analyst=analyst,
            )
        except Exception as e:
            print(f"[run_pipeline] ✗ Modelo {model_id} falló: {e}")
            failed.append((model_id, str(e)))

    if failed:
        print(f"\n[run_pipeline] {len(failed)}/{len(model_ids)} modelos fallaron:")
        for mid, err in failed:
            print(f"  ✗ {mid}: {err}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
