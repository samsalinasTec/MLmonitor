#!/usr/bin/env bash
# Orquesta los pasos del pipeline semanal dentro de la task de Fargate:
#   1) Sincroniza los CSVs semanales desde S3 al filesystem del contenedor.
#   2) Corre el ETL incremental (auto-detecta la semana desde MAX(semana_observacion)).
#   3) Corre el pipeline (métricas + PDF + S3 + SES).
#
# Credenciales AWS: por task role de ECS (no hay secretos embebidos).
# DB_URL, S3_BUCKET, S3_PREFIX, INPUTS_BUCKET, INPUTS_PREFIX, BEDROCK_MODEL_ID
# se inyectan por la task definition y los lee config/settings.py + secrets_loader.

set -euo pipefail

INPUTS_BUCKET="${INPUTS_BUCKET:?INPUTS_BUCKET no definido}"
INPUTS_PREFIX="${INPUTS_PREFIX:-inputs/raw_tables}"
RAW_DIR="/app/data/inputs/raw_tables"

mkdir -p "${RAW_DIR}" /tmp/artifacts/reports

echo "[entrypoint] Sincronizando inputs desde s3://${INPUTS_BUCKET}/${INPUTS_PREFIX}/ → ${RAW_DIR}/"
aws s3 sync "s3://${INPUTS_BUCKET}/${INPUTS_PREFIX}/" "${RAW_DIR}/" --only-show-errors

echo "[entrypoint] Inputs disponibles:"
ls -lh "${RAW_DIR}/"

# Env vars opcionales para ad-hoc:
#   RUN_DATE=YYYY-MM-DD  → fuerza --date en ambos scripts
#   SKIP_ETL=1           → salta el ETL (regenera solo PDF si datos ya están en RDS)
#   NO_EMAIL=1           → pasa --no-email al pipeline
#   NO_LLM=1             → pasa --no-llm al pipeline
# Ver ADR §8.2.21 y docs/curso/12_operacion_diaria.md.

DATE_ARG=""
if [ -n "${RUN_DATE:-}" ]; then
  DATE_ARG="--date ${RUN_DATE}"
  echo "[entrypoint] RUN_DATE override: ${RUN_DATE}"
fi

if [ "${SKIP_ETL:-0}" = "1" ]; then
  echo "[entrypoint] SKIP_ETL=1, saltando ETL."
else
  echo "[entrypoint] ETL incremental ${DATE_ARG}..."
  poetry run python scripts/run_incremental_etl.py ${DATE_ARG}
fi

PIPELINE_FLAGS="${DATE_ARG}"
[ "${NO_EMAIL:-0}" = "1" ] && PIPELINE_FLAGS="${PIPELINE_FLAGS} --no-email" && echo "[entrypoint] NO_EMAIL=1"
[ "${NO_LLM:-0}" = "1" ]   && PIPELINE_FLAGS="${PIPELINE_FLAGS} --no-llm"   && echo "[entrypoint] NO_LLM=1"

echo "[entrypoint] Pipeline (métricas + reporte + S3 + SES) ${PIPELINE_FLAGS}..."
poetry run python scripts/run_pipeline.py ${PIPELINE_FLAGS}

echo "[entrypoint] Done."
