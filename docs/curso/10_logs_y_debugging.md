# Módulo 10 — Logs y debugging

## Objetivo

Diagnosticar una task fallida desde cero usando CloudWatch Logs y `describe-tasks`. Saber qué preguntar primero cuando el Scheduler reporta un fallo.

## Conceptos

**Log group** = contenedor de streams. El nuestro: `/ecs/mlmonitor`.

**Log stream** = flujo lineal de logs, uno por task. Nombre: `run/mlmonitor/<task-id>` (el prefijo `run` viene del `awslogs-stream-prefix` en la task def).

**Retención** = cuánto tiempo se guardan. Configuramos 30 días.

**ECS `stopCode` / `stoppedReason`** = razón estructurada por la que una task murió. Valores comunes:
- `EssentialContainerExited` + `Essential container ... exited` → tu contenedor salió (mira el exit code).
- `TaskFailedToStart` → ECS no pudo arrancar (falta imagen, red, IAM).

## Track A — Seguimiento en vivo

```bash
# Seguir todos los logs del log group
aws logs tail /ecs/mlmonitor --follow

# Solo una task
TASK_ID=<sufijo del task ARN>
aws logs tail /ecs/mlmonitor \
  --log-stream-name-prefix "run/mlmonitor/${TASK_ID}" --since 1h

# Buscar un string
aws logs filter-log-events --log-group-name /ecs/mlmonitor \
  --filter-pattern "ERROR" --max-items 20
```

## Diagnóstico paso a paso

Una task terminó. ¿Por qué?

### Paso 1: estado general

```bash
aws ecs describe-tasks --cluster mlmonitor-cluster --tasks <task-arn> \
  --query 'tasks[0].{status:lastStatus,stopCode:stopCode,reason:stoppedReason,exit:containers[0].exitCode,exitReason:containers[0].reason}'
```

Ejemplo real:
```json
{
  "status": "STOPPED",
  "stopCode": "EssentialContainerExited",
  "reason": "Essential container in task exited",
  "exit": 1,
  "exitReason": null
}
```

`exit=1` → mira los logs.

### Paso 2: logs

```bash
aws logs tail /ecs/mlmonitor --log-stream-name-prefix "run/mlmonitor/<task-id>" --since 1h
```

### Paso 3: errores comunes

| Síntoma en logs | Causa probable | Fix |
|---|---|---|
| `AccessDeniedException: ses:SendRawEmail` | Policy SES mal | Ver módulo 04 |
| `NoSuchBucket` / `AccessDenied s3` | Policy S3Read/Write | Ver módulo 08 |
| `psycopg2.OperationalError: could not connect` | SG de RDS o endpoint mal | Probar desde laptop con `psql` |
| Task no deja ni un log | Falló antes de arrancar | Mira `stoppedReason` a nivel task |
| `exec format error` | Imagen construida en arm64 | Rebuild con `--platform linux/amd64` |
| `ResourceInitializationError` pullando ECR | Sin IP pública o SG egress bloqueado | Verifica subnet pública + IGW |

## Smoke test completo

```bash
# Lanzar task
TASK_ARN=$(aws ecs run-task \
  --cluster mlmonitor-cluster \
  --launch-type FARGATE \
  --task-definition mlmonitor \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-0dcfd7651de484c9b,subnet-0e5ed52bc4d23416d],securityGroups=[sg-0c54b54ed399b471c],assignPublicIp=ENABLED}" \
  --count 1 --query 'tasks[0].taskArn' --output text)

TASK_ID=$(echo $TASK_ARN | awk -F/ '{print $NF}')
echo "Task: $TASK_ID"

# Seguir
aws logs tail /ecs/mlmonitor --log-stream-name-prefix "run/mlmonitor/${TASK_ID}" --follow
```

Cuando la task termine (Ctrl+C el `tail` si no corta solo):

```bash
aws ecs describe-tasks --cluster mlmonitor-cluster --tasks $TASK_ARN \
  --query 'tasks[0].containers[0].exitCode'
# Esperado: 0
```

## Ejercicios

1. Lanza una task manual y sigue sus logs hasta el exit 0.
2. Rompe intencionalmente: edita `taskdef.json` apuntando a `mlmonitor:nonexistent-tag`, registra revisión nueva, corre. Captura el `stoppedReason`.
3. Usa `filter-log-events` para contar cuántos "ERROR" tiene el último run.

## Checklist de dominio

- [ ] Sé cómo ver logs en vivo y de una task específica.
- [ ] Puedo interpretar `stopCode` y `exitCode`.
- [ ] Identifico 3 errores comunes y su fix.
- [ ] Sé la diferencia entre "no se pudo arrancar" y "arrancó y salió 1".

## Referencias

- [CloudWatch Logs tail](https://docs.aws.amazon.com/cli/latest/reference/logs/tail.html)
- [ECS task lifecycle](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-lifecycle.html)
