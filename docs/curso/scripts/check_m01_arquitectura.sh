#!/usr/bin/env bash
# Módulo 01 — verifica que los 7 servicios core de la arquitectura existen.
set -u
pass() { echo "✅ $1"; }
fail() { echo "❌ $1"; FAILED=1; }
FAILED=0

aws ecr describe-repositories --repository-names mlmonitor >/dev/null 2>&1 \
  && pass "ECR repo 'mlmonitor'" || fail "ECR repo no existe"

aws ecs describe-clusters --clusters mlmonitor-cluster --query 'clusters[0].status' --output text 2>/dev/null | grep -q ACTIVE \
  && pass "ECS cluster 'mlmonitor-cluster' ACTIVE" || fail "ECS cluster no ACTIVE"

aws ecs list-task-definitions --family-prefix mlmonitor --query 'taskDefinitionArns' --output text 2>/dev/null | grep -q mlmonitor \
  && pass "Task definition family 'mlmonitor' registrada" || fail "Task definition no existe"

aws scheduler get-schedule --name mlmonitor-weekly >/dev/null 2>&1 \
  && pass "Schedule 'mlmonitor-weekly'" || fail "Schedule no existe"

aws rds describe-db-instances --db-instance-identifier ml-monitoring-db >/dev/null 2>&1 \
  && pass "RDS instance 'ml-monitoring-db'" || fail "RDS no existe"

aws s3api head-bucket --bucket ml-monitoring-reports-credito 2>/dev/null \
  && pass "S3 bucket 'ml-monitoring-reports-credito'" || fail "S3 bucket no accesible"

aws logs describe-log-groups --log-group-name-prefix /ecs/mlmonitor --query 'logGroups[0].logGroupName' --output text 2>/dev/null | grep -q mlmonitor \
  && pass "CloudWatch log group '/ecs/mlmonitor'" || fail "Log group no existe"

exit $FAILED
