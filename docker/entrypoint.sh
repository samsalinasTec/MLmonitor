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

echo "[entrypoint] ETL incremental..."
poetry run python scripts/run_incremental_etl.py

echo "[entrypoint] Pipeline (métricas + reporte + S3 + SES)..."
poetry run python scripts/run_pipeline.py

echo "[entrypoint] Done."
