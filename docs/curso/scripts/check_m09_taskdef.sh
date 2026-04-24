#!/usr/bin/env bash
# Módulo 09 — verifica task definition.
set -u
pass() { echo "✅ $1"; }
fail() { echo "❌ $1"; FAILED=1; }
FAILED=0

TD=$(aws ecs describe-task-definition --task-definition mlmonitor 2>/dev/null)
[ -n "$TD" ] && pass "Task def mlmonitor accesible" || fail "Task def no existe"

CPU=$(echo "$TD" | jq -r '.taskDefinition.cpu')
MEM=$(echo "$TD" | jq -r '.taskDefinition.memory')
REV=$(echo "$TD" | jq -r '.taskDefinition.revision')
IMG=$(echo "$TD" | jq -r '.taskDefinition.containerDefinitions[0].image')

echo "   Revisión: $REV | CPU: $CPU | Mem: $MEM"
echo "   Imagen:   $IMG"

[ "$CPU" = "1024" ] && pass "CPU = 1024" || fail "CPU inesperado: $CPU"
[ "$MEM" = "4096" ] && pass "Mem = 4096" || fail "Mem inesperado: $MEM"
echo "$IMG" | grep -q "930067561911.dkr.ecr" && pass "Imagen apunta a ECR correcto" || fail "Imagen no es ECR mlmonitor"

exit $FAILED
