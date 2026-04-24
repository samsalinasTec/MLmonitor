#!/usr/bin/env bash
# Módulo 00 — verifica que el entorno local está listo.
# Read-only, no modifica nada.
set -u

pass() { echo "✅ $1"; }
fail() { echo "❌ $1"; FAILED=1; }

FAILED=0

command -v aws >/dev/null && pass "aws CLI instalado ($(aws --version 2>&1))" || fail "aws CLI no encontrado"
command -v docker >/dev/null && pass "docker instalado" || fail "docker no encontrado"
docker buildx version >/dev/null 2>&1 && pass "docker buildx disponible" || fail "docker buildx no disponible"
command -v jq >/dev/null && pass "jq instalado" || fail "jq no encontrado"
command -v psql >/dev/null && pass "psql instalado ($(psql --version))" || fail "psql no encontrado"
command -v poetry >/dev/null && pass "poetry instalado" || fail "poetry no encontrado"
command -v python3.11 >/dev/null && pass "python3.11 instalado" || fail "python3.11 no encontrado"

echo ""
echo "-- Identidad AWS --"
if IDENTITY=$(aws sts get-caller-identity 2>&1); then
  ACCOUNT=$(echo "$IDENTITY" | jq -r .Account)
  [ "$ACCOUNT" = "930067561911" ] && pass "Cuenta AWS: $ACCOUNT" || fail "Cuenta incorrecta: $ACCOUNT (esperada 930067561911)"
  echo "$IDENTITY" | jq -r '"   User: \(.Arn)"'
else
  fail "aws sts get-caller-identity falló: $IDENTITY"
fi

REGION=$(aws configure get region 2>/dev/null)
[ "$REGION" = "us-east-1" ] && pass "Región: $REGION" || fail "Región: '$REGION' (esperada us-east-1)"

exit $FAILED
