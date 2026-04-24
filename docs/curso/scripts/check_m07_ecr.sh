#!/usr/bin/env bash
# Módulo 07 — verifica ECR.
set -u
pass() { echo "✅ $1"; }
fail() { echo "❌ $1"; FAILED=1; }
FAILED=0

aws ecr describe-repositories --repository-names mlmonitor >/dev/null 2>&1 \
  && pass "Repo mlmonitor existe" || fail "Repo no existe"

TAGS=$(aws ecr describe-images --repository-name mlmonitor --query 'imageDetails[].imageTags[]' --output text 2>/dev/null)
echo "$TAGS" | grep -q latest && pass "Tag 'latest' presente" || fail "Tag 'latest' no encontrado"

SIZE=$(aws ecr describe-images --repository-name mlmonitor --image-ids imageTag=latest --query 'imageDetails[0].imageSizeInBytes' --output text 2>/dev/null)
echo "   Tamaño imagen latest: $(( SIZE / 1024 / 1024 )) MB"

exit $FAILED
