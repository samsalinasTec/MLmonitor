# AWS IAM — Matriz de permisos cross-service

Permisos de los roles IAM que ejecutan MLMonitor en AWS. Complementa a [`aws_secrets_manager.md`](./aws_secrets_manager.md) (permisos específicos del servicio de secretos) y a [`aws_deployment.md`](./aws_deployment.md) (inventario operativo).

---

## 1. Roles reales (MVP, desde 2026-04-23)

Tras la ADR §8.2.20 (ECS Fargate + EventBridge Scheduler, task única), los tres roles que existen en la cuenta son:

- **`mlmonitor-ecs-execution`** — execution role de la task. Lo asume ECS (no el código del contenedor). Permite bajar la imagen de ECR y escribir en CloudWatch Logs. Policy managed: `AmazonECSTaskExecutionRolePolicy`.
- **`mlmonitor-task`** — task role. Lo asume el proceso dentro del contenedor para leer Secrets Manager, invocar Bedrock, leer CSVs de S3 (inputs), escribir PDF en S3 (reports) y enviar correo por SES. Policy inline `mlmonitor-task-policy` documentada en §2.
- **`mlmonitor-scheduler-invoke`** — lo asume EventBridge Scheduler para invocar `ecs:RunTask` sobre la task definition y hacer `iam:PassRole` de los dos roles anteriores.

Los JSON fuente de trust policies e inline policies viven en `mlmonitor/deploy/iam/`.

**Nota histórica:** originalmente (§8.2.15) se planteó separar los roles en "Pipeline" y "ETL" porque los jobs iban a desplegarse por separado. Al formalizar la ADR §8.2.20 se consolidó en una sola task, así que la separación IAM quedó como un único `mlmonitor-task` con el superset de permisos. Si en el futuro se separan los jobs, se puede reusar §2 dividiéndolo.

---

## 2. Matriz de acciones (`mlmonitor-task` — policy inline vigente)

| Sid | Acción | Recurso | Por qué |
|---|---|---|---|
| Secrets | `secretsmanager:GetSecretValue` | `ml-monitoring/rds-*`, `ml-monitoring/SES-*` | Cargar DB_URL y sender/recipient de SES. |
| Bedrock | `bedrock:InvokeModel` | Inference profile `us.anthropic.claude-haiku-4-5-20251001-v1:0` **y** foundation model `anthropic.claude-haiku-4-5-20251001-v1:0` | Generar narrativa. Se necesitan ambos ARNs porque el inference profile enruta al modelo de fundación. |
| S3Read | `s3:GetObject`, `s3:ListBucket` | `ml-monitoring-reports-credito` (bucket) y `ml-monitoring-reports-credito/inputs/*` | Sync de CSVs semanales desde `inputs/raw_tables/`. |
| S3Write | `s3:PutObject` | `ml-monitoring-reports-credito/mlmonitor/reports/*` | Subir PDFs generados. |
| SES | `ses:SendRawEmail` con `Condition StringEquals ses:FromAddress=1206029@onuriscp.com` | Identities `1206029@onuriscp.com` **y** `samsalriu@gmail.com` | Hallazgo del smoke test 2026-04-23: SES exige permisos sobre **ambas** identities involucradas en el envío (sender y recipient) si Resource no es `*`. El `Condition` ancla el sender válido. |

Notas:

- Si se agregan destinatarios nuevos, hay que extender el Resource SES con sus ARNs de identity (además de verificarlos en SES).
- Conectividad de red a RDS (security group) es requisito operativo pero no un permiso IAM; ver `aws_deployment.md §1.2`.
- El rol `mlmonitor-ecs-execution` solo carga la managed policy `AmazonECSTaskExecutionRolePolicy` (ECR pull + CloudWatch Logs).
- El rol `mlmonitor-scheduler-invoke` lleva una inline policy con `ecs:RunTask` sobre `mlmonitor:*` (restringido al cluster por `Condition`) + `iam:PassRole` sobre los dos roles anteriores.

---

## 3. Por qué no se incluye más

- **Lectura de S3** (para CSVs raw) queda fuera hasta decidir si los CSVs productivos vivirán en S3 (duda D5). Si sí, el rol ETL necesitará `s3:GetObject` sobre el bucket de entrada.
- **CloudWatch Logs** lo otorga automáticamente el servicio de ejecución (ECS/Lambda) a través del execution role; no forma parte de la matriz de la aplicación.
- **KMS** solo sería necesario si los secretos usan CMK propia. Hoy usan la llave default de Secrets Manager.

---

## 4. Checklist de formalización (cerrado 2026-04-23)

- [x] Rol único `mlmonitor-task` justificado por consolidación en una sola task (ver §1 nota histórica).
- [x] Policies restringidas por ARN específico (no `*` salvo `Condition` en SES).
- [x] Trust policy `ecs-tasks.amazonaws.com` configurada para `mlmonitor-ecs-execution` y `mlmonitor-task`.
- [x] Trust policy `scheduler.amazonaws.com` configurada para `mlmonitor-scheduler-invoke`.
- [x] Verificación end-to-end con smoke test `aws ecs run-task` 2026-04-23 (exit 0).
- [x] ARNs reales documentados en `aws_deployment.md §1.4`.
