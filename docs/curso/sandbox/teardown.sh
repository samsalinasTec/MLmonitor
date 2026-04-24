#!/usr/bin/env bash
# Borra todos los recursos con sufijo -curso-$CURSO_ALIAS.
# Para usarse tras completar Track B del curso.
set -u

if [ -z "${CURSO_ALIAS:-}" ]; then
  echo "❌ Define CURSO_ALIAS primero: export CURSO_ALIAS=<tu-alias>"
  exit 1
fi

SUFFIX="-curso-${CURSO_ALIAS}"
echo "⚠️  Voy a borrar recursos con sufijo '$SUFFIX'"
read -p "¿Continuar? [y/N] " CONFIRM
[ "$CONFIRM" = "y" ] || exit 0

echo ""
echo "-- Schedules --"
for name in mlmonitor-test${SUFFIX}; do
  aws scheduler delete-schedule --name "$name" 2>/dev/null && echo "borrado $name" || echo "no existe $name"
done

echo ""
echo "-- ECR tags --"
for tag in "v0.0.0${SUFFIX}" "curso-latest-${CURSO_ALIAS}"; do
  aws ecr batch-delete-image --repository-name mlmonitor --image-ids imageTag=$tag >/dev/null 2>&1 \
    && echo "borrado tag $tag" || echo "no existe tag $tag"
done

echo ""
echo "-- RDS sandbox --"
DBI="mlmonitor-db-curso-${CURSO_ALIAS}"
aws rds delete-db-instance --db-instance-identifier "$DBI" --skip-final-snapshot --delete-automated-backups 2>/dev/null \
  && echo "borrando $DBI (tarda minutos)" || echo "no existe $DBI"

echo ""
echo "Teardown completado. Verifica en consola que no quedan recursos con sufijo '$SUFFIX'."
