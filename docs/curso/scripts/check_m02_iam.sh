#!/usr/bin/env bash
# Módulo 02 — verifica los 3 roles IAM y el SG de Fargate.
set -u
pass() { echo "✅ $1"; }
fail() { echo "❌ $1"; FAILED=1; }
FAILED=0

for R in mlmonitor-ecs-execution mlmonitor-task mlmonitor-scheduler-invoke; do
  aws iam get-role --role-name $R >/dev/null 2>&1 && pass "Rol $R existe" || fail "Rol $R no existe"
done

PRINCIPAL=$(aws iam get-role --role-name mlmonitor-task --query 'Role.AssumeRolePolicyDocument.Statement[0].Principal.Service' --output text 2>/dev/null)
[ "$PRINCIPAL" = "ecs-tasks.amazonaws.com" ] && pass "Trust de mlmonitor-task = ecs-tasks" || fail "Trust incorrecto: $PRINCIPAL"

PRINCIPAL=$(aws iam get-role --role-name mlmonitor-scheduler-invoke --query 'Role.AssumeRolePolicyDocument.Statement[0].Principal.Service' --output text 2>/dev/null)
[ "$PRINCIPAL" = "scheduler.amazonaws.com" ] && pass "Trust de scheduler-invoke = scheduler" || fail "Trust incorrecto: $PRINCIPAL"

aws ec2 describe-security-groups --group-ids sg-0c54b54ed399b471c >/dev/null 2>&1 \
  && pass "SG Fargate sg-0c54b54ed399b471c existe" || fail "SG no encontrado"

exit $FAILED
