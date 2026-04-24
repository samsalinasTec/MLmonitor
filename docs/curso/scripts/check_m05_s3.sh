#!/usr/bin/env bash
# Módulo 05 — verifica S3 bucket y prefijos.
set -u
pass() { echo "✅ $1"; }
fail() { echo "❌ $1"; FAILED=1; }
FAILED=0

BUCKET=ml-monitoring-reports-credito
aws s3api head-bucket --bucket $BUCKET 2>/dev/null && pass "Bucket $BUCKET existe" || fail "Bucket no accesible"

COUNT_INPUTS=$(aws s3 ls s3://$BUCKET/inputs/raw_tables/ 2>/dev/null | wc -l | tr -d ' ')
[ "$COUNT_INPUTS" -gt 0 ] && pass "Prefijo inputs/raw_tables/ tiene $COUNT_INPUTS objetos" || fail "inputs/raw_tables/ vacío"

COUNT_REPORTS=$(aws s3 ls s3://$BUCKET/mlmonitor/reports/ 2>/dev/null | wc -l | tr -d ' ')
[ "$COUNT_REPORTS" -gt 0 ] && pass "Prefijo mlmonitor/reports/ tiene $COUNT_REPORTS PDFs" || echo "⚠️  mlmonitor/reports/ vacío (ok si aún no corre pipeline)"

exit $FAILED
