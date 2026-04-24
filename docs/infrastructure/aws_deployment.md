# AWS Deployment — MLMonitor

Runbook operativo del MVP en la nube. Documenta la infraestructura real creada, cómo disparar el pipeline (manual o programado), cómo leer logs, y cómo hacer rollback. Contexto de la decisión: ver [`../decisions.md` §8.2.20](../decisions.md). Para permisos IAM por servicio ver [`aws_iam.md`](./aws_iam.md), y para secretos [`aws_secrets_manager.md`](./aws_secrets_manager.md).

---

## 1. Inventario de recursos

Todo en cuenta **`930067561911`**, región **`us-east-1`**.

### 1.1 Compute

| Recurso | Identificador | Nota |
|---|---|---|
| ECR repo | `930067561911.dkr.ecr.us-east-1.amazonaws.com/mlmonitor` | Tags `v0.1.0`, `latest`. |
| ECS cluster | `mlmonitor-cluster` | Fargate only, sin capacity providers custom. |
| Task definition | `mlmonitor` (familia), revisión actual `:1` | CPU 1024, memoria 4096, arch X86_64, linux. |
| Log group | `/ecs/mlmonitor` | Retención 30 días; stream prefix `run`. |

### 1.2 Network

| Recurso | Identificador |
|---|---|
| VPC | `vpc-0290fa17b63fe4814` (default) |
| Subnets en uso por la task | `subnet-0dcfd7651de484c9b` (us-east-1a), `subnet-0e5ed52bc4d23416d` (us-east-1b) — ambas públicas |
| SG Fargate | `sg-0c54b54ed399b471c` (`mlmonitor-fargate-sg`) — solo egress |
| SG RDS | `sg-02e9d008b587402f7` (default VPC) — **deuda: abierto 5432 a `0.0.0.0/0`** |
| IGW | `igw-05d2cf7377609e8a2` |

Las tasks se lanzan con `assignPublicIp=ENABLED` para que puedan hablar con ECR, Bedrock, SES, S3 y Secrets Manager por el IGW.

### 1.3 Datos

| Recurso | Identificador |
|---|---|
| RDS Postgres 16 | `ml-monitoring-db.cepye8aei35e.us-east-1.rds.amazonaws.com:5432`, DB `mlmonitor`, usuario `mlmonitor_admin` |
| S3 bucket | `ml-monitoring-reports-credito` |
| S3 inputs | `s3://ml-monitoring-reports-credito/inputs/raw_tables/` |
| S3 reports | `s3://ml-monitoring-reports-credito/mlmonitor/reports/` |
| Secrets Manager | `ml-monitoring/rds`, `ml-monitoring/SES` |

### 1.4 IAM

| Rol | Uso |
|---|---|
| `mlmonitor-ecs-execution` | Execution role de la task. Managed policy `AmazonECSTaskExecutionRolePolicy` (ECR pull + CloudWatch Logs). |
| `mlmonitor-task` | Task role. Inline policy `mlmonitor-task-policy` con Secrets Manager, Bedrock InvokeModel, S3 Read inputs / Write reports, SES SendRawEmail (con `Condition ses:FromAddress=1206029@onuriscp.com`). |
| `mlmonitor-scheduler-invoke` | Usado por EventBridge Scheduler. `ecs:RunTask` sobre la family `mlmonitor:*` + `iam:PassRole` sobre los dos roles anteriores. |

Los JSON fuente de las policies viven en `mlmonitor/deploy/iam/`.

### 1.5 Scheduler

| Recurso | Valor |
|---|---|
| Nombre | `mlmonitor-weekly` |
| Cron | `cron(0 14 ? * MON *)` UTC |
| Equivalente local | Lunes 08:00 América/Ciudad_de_México |
| Estado | `ENABLED` |
| Target | ECS RunTask sobre `mlmonitor-cluster` + task definition `mlmonitor` |

---

## 2. Variables de entorno de la task

Configuradas en `mlmonitor/deploy/taskdef.json`. Lo sensible (DB_URL, emails) lo carga el código desde Secrets Manager al arrancar, no por env.

| Variable | Valor |
|---|---|
| `AWS_REGION` | `us-east-1` |
| `S3_BUCKET` | `ml-monitoring-reports-credito` |
| `S3_PREFIX` | `mlmonitor/reports` |
| `INPUTS_BUCKET` | `ml-monitoring-reports-credito` |
| `INPUTS_PREFIX` | `inputs/raw_tables` |
| `BEDROCK_MODEL_ID` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| `ARTIFACTS_DIR` | `/tmp/artifacts` |

---

## 3. Runbooks

### 3.1 Flujo operativo normal

1. El lunes a las 08:00 CDMX (14:00 UTC) EventBridge Scheduler dispara la task.
2. El contenedor hace `aws s3 sync` de `inputs/raw_tables/` al filesystem local.
3. Corre `run_incremental_etl.py` (auto-detecta la semana desde `MAX(semana_observacion)` del CSV).
4. Corre `run_pipeline.py` (auto-detecta `calculation_date` desde `MAX(origination_week)` en RDS, genera PDF, sube a S3, envía por SES).
5. La task termina con exit 0. Los logs quedan en CloudWatch 30 días.

### 3.2 Subir los CSVs semanales

```bash
aws s3 cp variables_serc_<YYYYWW>.csv s3://ml-monitoring-reports-credito/inputs/raw_tables/
aws s3 cp muestra_weekly_<YYYYWW>.csv  s3://ml-monitoring-reports-credito/inputs/raw_tables/
```

El `base_train_test_bb.csv` ya vive en el bucket y no se reemplaza salvo re-entrenamiento del modelo (ver dudas D6).

### 3.3 Disparo manual (fuera de horario o re-ejecución)

```bash
aws ecs run-task \
  --cluster mlmonitor-cluster \
  --launch-type FARGATE \
  --task-definition mlmonitor \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-0dcfd7651de484c9b,subnet-0e5ed52bc4d23416d],securityGroups=[sg-0c54b54ed399b471c],assignPublicIp=ENABLED}" \
  --count 1
```

Esto funciona independientemente del estado del Scheduler — no hace falta deshabilitarlo para disparar a mano.

### 3.4 Seguir los logs en vivo

```bash
aws logs tail /ecs/mlmonitor --follow
```

Para logs de una task específica:

```bash
aws logs tail /ecs/mlmonitor --log-stream-name-prefix "run/mlmonitor/<task-id>" --since 1h
```

El `<task-id>` es el sufijo del ARN que devuelve `aws ecs run-task`.

### 3.5 Describir el estado de una task

```bash
aws ecs describe-tasks --cluster mlmonitor-cluster --tasks <task-arn> \
  --query 'tasks[0].{status:lastStatus,stopCode:stopCode,stoppedReason:stoppedReason,exit:containers[0].exitCode}'
```

### 3.6 Promover una nueva imagen

```bash
cd mlmonitor
VERSION=v0.1.1
docker buildx build --platform linux/amd64 \
  -t 930067561911.dkr.ecr.us-east-1.amazonaws.com/mlmonitor:${VERSION} \
  -t 930067561911.dkr.ecr.us-east-1.amazonaws.com/mlmonitor:latest \
  --push .

# Si cambias algo en la task definition, registrar una revisión nueva:
aws ecs register-task-definition --cli-input-json file://deploy/taskdef.json
```

El Scheduler apunta al alias de la familia (`mlmonitor` sin sufijo), así que usa siempre la revisión más reciente.

### 3.7 Pausar / reanudar el Scheduler

```bash
aws scheduler update-schedule --name mlmonitor-weekly --state DISABLED ...
aws scheduler update-schedule --name mlmonitor-weekly --state ENABLED  ...
```

(`update-schedule` requiere reenviar target y expresión; ver `deploy/scheduler-target.json`.)

### 3.8 Rollback

- **Imagen defectuosa:** retagear una anterior como `latest` en ECR.
  ```bash
  MANIFEST=$(aws ecr batch-get-image --repository-name mlmonitor --image-ids imageTag=v0.1.0 --query 'images[0].imageManifest' --output text)
  aws ecr put-image --repository-name mlmonitor --image-tag latest --image-manifest "$MANIFEST"
  ```
- **DB corrompida:** restaurar `data/backups/rds_pre_reset_2026-04-23.sql` (o backup más reciente) con `psql -f`.
- **Scheduler haciendo daño:** `aws scheduler update-schedule --state DISABLED ...`.

### 3.9 Leer secretos (solo para depurar)

```bash
aws secretsmanager get-secret-value --secret-id ml-monitoring/rds --query SecretString --output text
aws secretsmanager get-secret-value --secret-id ml-monitoring/SES --query SecretString --output text
```

---

## 4. Archivos en el repo relevantes al deploy

- `mlmonitor/Dockerfile` — imagen del pipeline.
- `mlmonitor/docker/entrypoint.sh` — sync + ETL + Pipeline.
- `mlmonitor/.dockerignore` — exclusiones del build.
- `mlmonitor/deploy/taskdef.json` — task definition.
- `mlmonitor/deploy/scheduler-target.json` — target del schedule.
- `mlmonitor/deploy/iam/trust-ecs-tasks.json` — trust policy para los dos roles de ECS.
- `mlmonitor/deploy/iam/mlmonitor-task-policy.json` — policy inline del task role.
- `mlmonitor/deploy/iam/trust-scheduler.json` — trust policy del rol del Scheduler.
- `mlmonitor/deploy/iam/mlmonitor-scheduler-policy.json` — policy del rol del Scheduler.
- `mlmonitor/data/backups/rds_pre_reset_2026-04-23.sql` — dump previo al reset de F0.

---

## 5. Deuda técnica (TODOs con prioridad)

1. **Cerrar SG de RDS.** Hoy `sg-02e9d008b587402f7` acepta `0.0.0.0/0:5432`. Dejar solo al `sg-0c54b54ed399b471c` y a las IPs de desarrollo autorizadas.
2. **Salir de SES sandbox.** Ticket a AWS Support. Hoy solo se puede enviar a identidades verificadas.
3. **Escribir Terraform.** Usar `terraform import` sobre lo creado manualmente. Estructura sugerida: módulos `compute/`, `network/`, `iam/`, `scheduler/`.
4. **CI/CD.** GitHub Actions que en cada push a `main` construya y pushee la imagen con el SHA como tag, y reciba un gate manual para promover a `latest`.
5. **Lifecycle policy en S3.** PDFs viejos a Glacier (ej. >180 días); CSVs de inputs con versioning + retención.
6. **Hardening de red.** RDS en subnets privadas + NAT Gateway, Fargate en subnets privadas. Requiere crear VPC nueva o modificar la default con cuidado.
7. **Quitar `aws_cli` del contenedor** si encontramos una forma Python-nativa de hacer el sync con `boto3` — ahorra ~100MB de imagen.
8. **`FACT_PERFORMANCE_OUTCOMES`** apareció en el dump de RDS pero no está en `db/models.py`. Validar si es tabla viva o residuo — si residuo, dropear.
