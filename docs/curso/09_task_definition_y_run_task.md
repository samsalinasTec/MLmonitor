# Módulo 09 — Task definition y RunTask

## Objetivo

Leer `deploy/taskdef.json` completo, registrar una revisión nueva, y disparar `aws ecs run-task` manualmente con las opciones correctas.

## Conceptos

**Task definition** = receta versionada de cómo correr el contenedor. Cada cambio crea una **revisión** (`mlmonitor:1`, `:2`, …). Las revisiones son inmutables.

**Family** = prefijo compartido de revisiones (`mlmonitor`).

**RunTask** = lanzar una ejecución one-shot de la task. No confundir con **Service** (tasks long-running con auto-restart).

**`networkMode: awsvpc`** = cada task tiene su propia ENI con IP propia en tu VPC. Obligatorio para Fargate.

**`assignPublicIp: ENABLED`** = la ENI recibe IP pública. Necesario porque corremos en subnet pública y salimos al internet por IGW.

## Lectura de `deploy/taskdef.json`

```json
{
  "family": "mlmonitor",
  "requiresCompatibilities": ["FARGATE"],
  "networkMode": "awsvpc",
  "cpu": "1024",        // 1 vCPU
  "memory": "4096",     // 4 GB
  "runtimePlatform": { "cpuArchitecture": "X86_64", "operatingSystemFamily": "LINUX" },
  "executionRoleArn": "arn:...role/mlmonitor-ecs-execution",
  "taskRoleArn": "arn:...role/mlmonitor-task",
  "containerDefinitions": [
    {
      "name": "mlmonitor",
      "image": "930067561911.dkr.ecr.us-east-1.amazonaws.com/mlmonitor:latest",
      "environment": [
        { "name": "AWS_REGION", "value": "us-east-1" },
        { "name": "S3_BUCKET", "value": "ml-monitoring-reports-credito" },
        ...
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/mlmonitor",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "run"
        }
      }
    }
  ]
}
```

**Decisiones de sizing:** 1 vCPU / 4 GB inicial. Métricas (PSI, Gini, KS) son CPU-bound; generación de PDF + Bedrock son I/O-bound. Tras primera ejecución real: picos de ~2.5 GB RAM, 60% CPU. Margen OK.

**Fargate price bands:** Fargate cobra por combinación fija de CPU/mem (ver [pricing](https://aws.amazon.com/fargate/pricing/)). 1024/4096 es una combinación válida; 1024/3072 también.

## Track A — Inspección

```bash
# Lista de revisiones
aws ecs list-task-definitions --family-prefix mlmonitor

# Detalle de la última
aws ecs describe-task-definition --task-definition mlmonitor \
  --query 'taskDefinition.{rev:revision,cpu:cpu,mem:memory,image:containerDefinitions[0].image}'

# Tasks actualmente en el cluster
aws ecs list-tasks --cluster mlmonitor-cluster
```

## Registrar una revisión nueva

Cuando cambias env vars, sizing, o la imagen en la task def:

```bash
cd mlmonitor
# Editas deploy/taskdef.json ...
aws ecs register-task-definition --cli-input-json file://deploy/taskdef.json \
  --query 'taskDefinition.{family:family,rev:revision}'
# {"family": "mlmonitor", "rev": 2}
```

El Scheduler apunta a la family `mlmonitor` sin revisión → recoge la nueva automáticamente en el siguiente disparo.

## Disparo manual de una task

```bash
aws ecs run-task \
  --cluster mlmonitor-cluster \
  --launch-type FARGATE \
  --task-definition mlmonitor \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-0dcfd7651de484c9b,subnet-0e5ed52bc4d23416d],securityGroups=[sg-0c54b54ed399b471c],assignPublicIp=ENABLED}" \
  --count 1 \
  --query 'tasks[0].taskArn'
```

La `--task-definition mlmonitor` sin sufijo = última revisión. Para forzar una específica: `mlmonitor:3`.

## Override de env vars en una ejecución

Útil para backfill o re-ejecución por fecha (ver módulo 12):

```bash
aws ecs run-task \
  --cluster mlmonitor-cluster \
  --launch-type FARGATE \
  --task-definition mlmonitor \
  --network-configuration "awsvpcConfiguration={...}" \
  --overrides '{
    "containerOverrides": [{
      "name": "mlmonitor",
      "environment": [{"name": "RUN_DATE", "value": "2026-01-05"}]
    }]
  }'
```

> ⚠️ Requiere que el `entrypoint.sh` lea `$RUN_DATE` — hoy no lo hace. Propuesta de 3 líneas en módulo 12.

## Ejercicios

1. Muestra la revisión más alta de `mlmonitor` y qué imagen apunta.
2. Corre `aws ecs run-task` idéntico al de arriba y captura el `taskArn`. Úsalo en módulo 10 para seguir logs.
3. Simula un sizing distinto: edita `cpu:"2048"`, `memory:"4096"` (combinación válida), registra y describe la revisión nueva. No la uses — déjala ahí.

## Checklist de dominio

- [ ] Entiendo family vs revision.
- [ ] Sé qué hace `assignPublicIp=ENABLED` y cuándo quitarlo.
- [ ] Puedo registrar una revisión nueva y correr una task manual.
- [ ] Sé cómo hacer override de env sin modificar la task definition.

## Referencias

- [Task definition parameters](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters.html)
- [RunTask API](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_RunTask.html)
