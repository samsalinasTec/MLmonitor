#!/usr/bin/env bash
# Módulo 11 — verifica EventBridge Scheduler.
set -u
pass() { echo "✅ $1"; }
fail() { echo "❌ $1"; FAILED=1; }
FAILED=0

S=$(aws scheduler get-schedule --name mlmonitor-weekly 2>/dev/null)
[ -n "$S" ] && pass "Schedule mlmonitor-weekly existe" || { fail "Schedule no existe"; exit 1; }

STATE=$(echo "$S" | jq -r '.State')
EXPR=$(echo "$S" | jq -r '.ScheduleExpression')
TZ=$(echo "$S" | jq -r '.ScheduleExpressionTimezone')

echo "   Estado: $STATE | Cron: $EXPR | TZ: $TZ"
[ "$STATE" = "ENABLED" ] && pass "Schedule ENABLED" || echo "⚠️  Schedule $STATE (esperado ENABLED)"
[ "$EXPR" = "cron(0 14 ? * MON *)" ] && pass "Cron correcto" || fail "Cron inesperado: $EXPR"

exit $FAILED
