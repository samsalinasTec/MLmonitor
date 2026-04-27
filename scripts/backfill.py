"""
backfill.py — Orquestador local para poblar histórico de FACT_* en RDS.

Itera semanas (lunes ISO) desde --start hasta --end y ejecuta:
  1) run_incremental_etl.py --date <semana>
  2) run_pipeline.py --date <semana> --no-email --no-llm

Diseñado para correr DESDE LAPTOP, no desde ECS. One-shot. Es 100%
orquestación: usa subprocess para invocar los scripts existentes; no
toca lógica de negocio.

Inyecta S3_BUCKET="" en el environment del subprocess para deshabilitar
la subida de PDFs a S3 (config/settings.py respeta ese contrato — ver
CLAUDE.md §2). Los PDFs igualmente se generan en artifacts/reports/
local; bórralos cuando termines: rm -rf artifacts/reports/.

Uso:
    poetry run python scripts/backfill.py --start 2025-09-01 --end 2026-01-05
    poetry run python scripts/backfill.py --start 2025-10-13 --end 2025-10-13  # una sola
    poetry run python scripts/backfill.py --start 2025-09-01 --end 2026-01-05--skip-etl

Idempotencia: las tablas FACT tienen UniqueConstraint sobre la clave de
negocio. Re-correr una semana ya cargada no duplica filas.
"""

import argparse
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser(description="Backfill local de FACT_* + FACT_METRICS_HISTORY")
    p.add_argument("--start", required=True, help="Lunes ISO de inicio (YYYY-MM-DD)")
    p.add_argument("--end",   required=True, help="Lunes ISO de fin (YYYY-MM-DD), inclusivo")
    p.add_argument("--skip-etl", action="store_true",
                   help="Solo corre el pipeline (datos ya en RDS).")
    p.add_argument("--continue-on-error", action="store_true",
                   help="Si una semana falla, sigue con la siguiente (default: para).")
    return p.parse_args()


def run(cmd, env):
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, env=env).returncode


def main():
    args = parse_args()

    start = date.fromisoformat(args.start)
    end   = date.fromisoformat(args.end)
    if start.weekday() != 0 or end.weekday() != 0:
        sys.exit("ERROR: --start y --end deben ser lunes ISO (weekday=0).")
    if end < start:
        sys.exit("ERROR: --end < --start.")

    project_root = Path(__file__).parent.parent
    env = os.environ.copy()
    env["S3_BUCKET"] = ""  # deshabilita subida a S3 (CLAUDE.md §2)

    weeks = []
    current = start
    while current <= end:
        weeks.append(current)
        current += timedelta(weeks=1)

    print(f"[backfill] {len(weeks)} semanas: {weeks[0]} ... {weeks[-1]}")
    print(f"[backfill] skip_etl={args.skip_etl}  S3 upload deshabilitado")
    print()

    failed = []
    for w in weeks:
        ws = w.isoformat()
        print(f"=== {ws} ===")

        if not args.skip_etl:
            rc = run(["poetry", "run", "python", "scripts/run_incremental_etl.py", "--date", ws], env)
            if rc != 0:
                failed.append((ws, "etl"))
                print(f"  [skip pipeline para {ws}: ETL falló]")
                if not args.continue_on_error:
                    break
                continue

        rc = run(["poetry", "run", "python", "scripts/run_pipeline.py",
                  "--date", ws, "--no-email", "--no-llm"], env)
        if rc != 0:
            failed.append((ws, "pipeline"))
            if not args.continue_on_error:
                break

    print()
    print(f"[backfill] Total: {len(weeks)} | OK: {len(weeks) - len(failed)} | Fallidas: {len(failed)}")
    for ws, stage in failed:
        print(f"  ✗ {ws} ({stage})")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
