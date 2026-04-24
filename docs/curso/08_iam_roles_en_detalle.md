# Módulo 08 — IAM roles en detalle

## Objetivo

Leer cada uno de los 3 roles JSON línea por línea y poder explicarlos en una pizarra.

## Los 3 roles

### 1. `mlmonitor-ecs-execution`

**Trust** ([`deploy/iam/trust-ecs-tasks.json`](../../deploy/iam/trust-ecs-tasks.json)):

```json
{ "Principal": { "Service": "ecs-tasks.amazonaws.com" },
  "Action": "sts:AssumeRole" }
```

**Permissions:** managed policy `AmazonECSTaskExecutionRolePolicy`. Contiene:
- `ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`, `ecr:GetDownloadUrlForLayer`, `ecr:BatchGetImage` — para pullear la imagen.
- `logs:CreateLogStream`, `logs:PutLogEvents` — para escribir logs.

**Quién lo asume:** ECS mismo, **antes** de arrancar el contenedor. No es tu código.

### 2. `mlmonitor-task`

**Trust:** idéntico al execution role (`ecs-tasks.amazonaws.com`). La diferencia es cuándo se asume: **después** de arrancar el contenedor, por el proceso Python.

**Permissions** ([`deploy/iam/mlmonitor-task-policy.json`](../../deploy/iam/mlmonitor-task-policy.json)):

| Sid | Acción | Recurso | Propósito |
|---|---|---|---|
| Secrets | `secretsmanager:GetSecretValue` | `ml-monitoring/rds-*`, `ml-monitoring/SES-*` | Cargar credenciales al arrancar |
| Bedrock | `bedrock:InvokeModel` | inference profile + foundation model | Narrativa del reporte |
| S3Read | `s3:GetObject`, `s3:ListBucket` | bucket + `inputs/*` | Sync de CSVs |
| S3Write | `s3:PutObject` | `mlmonitor/reports/*` | Subir PDF |
| SES | `ses:SendRawEmail` | 2 identities + Condition | Enviar correo |

**Por qué `-*` al final de los ARNs de Secrets:** Secrets Manager agrega un sufijo hash al ARN real, e.g. `ml-monitoring/rds-AbC123`. Sin el wildcard, la policy nunca matchea.

### 3. `mlmonitor-scheduler-invoke`

**Trust** ([`deploy/iam/trust-scheduler.json`](../../deploy/iam/trust-scheduler.json)):

```json
{ "Principal": { "Service": "scheduler.amazonaws.com" } }
```

**Permissions** ([`deploy/iam/mlmonitor-scheduler-policy.json`](../../deploy/iam/mlmonitor-scheduler-policy.json)):

```json
[
  { "Action": "ecs:RunTask",
    "Resource": "arn:aws:ecs:us-east-1:930067561911:task-definition/mlmonitor:*",
    "Condition": { "ArnLike": { "ecs:cluster": "arn:...cluster/mlmonitor-cluster" } } },
  { "Action": "iam:PassRole",
    "Resource": [
      "arn:aws:iam::930067561911:role/mlmonitor-ecs-execution",
      "arn:aws:iam::930067561911:role/mlmonitor-task"
    ] }
]
```

**Lectura:** puede lanzar cualquier revisión de la family `mlmonitor` (el `*` al final) pero **solo** en el cluster `mlmonitor-cluster` (Condition). Puede entregar a ECS los dos roles anteriores.

## Principio de mínimo privilegio — aplicación real

**Mal (evitamos):**
```json
{ "Action": "s3:*", "Resource": "*" }
```

**Bien (hicimos):**
```json
{ "Action": ["s3:GetObject", "s3:ListBucket"],
  "Resource": ["arn:aws:s3:::bucket-name", "arn:aws:s3:::bucket-name/inputs/*"] }
```

Cada `Action` se lista explícitamente; cada `Resource` es un ARN específico (con wildcards solo donde necesarios).

## Track A — Inspección

```bash
for R in mlmonitor-ecs-execution mlmonitor-task mlmonitor-scheduler-invoke; do
  echo "=== $R ==="
  aws iam get-role --role-name $R \
    --query 'Role.{created:CreateDate,arn:Arn,trust:AssumeRolePolicyDocument.Statement[0].Principal}'
  echo "-- attached --"
  aws iam list-attached-role-policies --role-name $R --query 'AttachedPolicies[].PolicyArn'
  echo "-- inline --"
  aws iam list-role-policies --role-name $R --query 'PolicyNames'
done

# Ver el JSON completo de la inline policy del task role
aws iam get-role-policy --role-name mlmonitor-task --policy-name mlmonitor-task-policy \
  --query 'PolicyDocument'
```

## Ejercicios

1. Lee la inline policy del task role y para cada `Sid` escribe en una línea qué pasaría si quitas ese statement.
2. Modifica mentalmente la policy para que el task role **también** pueda leer el bucket `mlmonitor-feedback` (hipotético). ¿Qué Sid cambias?
3. Simula un intento de `ecs:RunTask` en otro cluster con el scheduler role (en papel): ¿falla? ¿Por qué? (Pista: Condition `ecs:cluster`.)

## Problemas que encontré

- **Policy de SES autorizaba solo sender.** Ya cubierto en módulo 04.
- **Wildcard en Secrets.** Inicialmente puse `arn:aws:secretsmanager:...:secret:ml-monitoring/rds` sin `-*`. AccessDenied. Debugged con `aws sts decode-authorization-message`.

## Checklist de dominio

- [ ] Puedo recitar los 5 `Sid` del task role y qué hace cada uno.
- [ ] Sé por qué los ARNs de Secrets llevan `-*`.
- [ ] Entiendo por qué el scheduler role necesita `iam:PassRole`.
- [ ] Identifico qué hace cada `Condition` (SES:FromAddress, ecs:cluster).

## Referencias

- [AWS managed policies for ECS](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/security-iam-awsmanpol.html)
- Interno: [`docs/infrastructure/aws_iam.md`](../infrastructure/aws_iam.md)
