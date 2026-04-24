#!/usr/bin/env bash
# Módulo 03 — verifica RDS + Secrets Manager.
set -u
pass() { echo "✅ $1"; }
fail() { echo "❌ $1"; FAILED=1; }
FAILED=0

ENGINE=$(aws rds describe-db-instances --db-instance-identifier ml-monitoring-db --query 'DBInstances[0].Engine' --output text 2>/dev/null)
[ "$ENGINE" = "postgres" ] && pass "RDS engine = postgres" || fail "RDS engine: $ENGINE"

VER=$(aws rds describe-db-instances --db-instance-identifier ml-monitoring-db --query 'DBInstances[0].EngineVersion' --output text 2>/dev/null)
echo "$VER" | grep -qE '^16\.' && pass "RDS version $VER" || fail "RDS version inesperada: $VER"

for S in ml-monitoring/rds ml-monitoring/SES; do
  aws secretsmanager describe-secret --secret-id $S >/dev/null 2>&1 \
    && pass "Secret $S existe" || fail "Secret $S no existe"
done

# Conectividad (sin exponer password)
export PGPASSWORD=$(aws secretsmanager get-secret-value --secret-id ml-monitoring/rds --query SecretString --output text 2>/dev/null | jq -r '.password')
if [ -n "$PGPASSWORD" ]; then
  if psql -h ml-monitoring-db.cepye8aei35e.us-east-1.rds.amazonaws.com \
          -U mlmonitor_admin -d mlmonitor -c "SELECT 1" >/dev/null 2>&1; then
    pass "psql conecta a RDS"
  else
    fail "psql no conecta (revisa SG / credenciales / versión de psql)"
  fi
else
  fail "No pude leer password del secreto"
fi

exit $FAILED
