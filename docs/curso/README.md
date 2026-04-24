# Curso — AWS Deployment de MLMonitor

Material didáctico para aprender **replicando** lo que ya está desplegado en producción. El MVP en AWS ya corre (desde 2026-04-23); este curso te lleva desde "no sé por qué existen 3 roles IAM" hasta "puedo operar esto sin ayuda y razonar cuándo romperlo".

## Cómo usar este curso

Cada módulo tiene dos tracks:

- **Track A — Inspección read-only.** Corres comandos `aws ... describe ...` contra la cuenta real (`930067561911`, `us-east-1`) y verificas que entiendes lo que ves. **No modifica nada.** Es el track recomendado para la primera pasada.
- **Track B — Recrear desde cero (opcional).** En una cuenta sandbox o con sufijos `-curso-<tu-alias>`, replicas los recursos desde cero. Ideal para afianzar. Al final, `sandbox/teardown.sh` borra lo que creaste.

Al final de cada módulo hay un **Checklist de dominio** — si no puedes marcar cada casilla, regresa.

## Orden sugerido

Lee en orden. Cada módulo asume los anteriores pero intenta ser self-contained con enlaces.

| # | Módulo | Tiempo estimado | Depende de |
|---|---|---|---|
| 00 | [Setup y prerrequisitos](00_setup_y_prerrequisitos.md) | 30 min | — |
| 01 | [Arquitectura general](01_arquitectura_general.md) | 30 min | 00 |
| 02 | [Fundamentos AWS (IAM, VPC, SG)](02_aws_fundamentos.md) | 60 min | 01 |
| 03 | [Datos: RDS y Secrets Manager](03_datos_rds_y_secrets.md) | 45 min | 02 |
| 04 | [Bedrock y SES](04_bedrock_y_ses.md) | 45 min | 02 |
| 05 | [S3 buckets y prefijos](05_s3_buckets_y_prefijos.md) | 30 min | 02 |
| 06 | [Dockerfile y WeasyPrint](06_dockerfile_y_weasyprint.md) | 60 min | 00 |
| 07 | [ECR: push de imagen](07_ecr_push_imagen.md) | 30 min | 06 |
| 08 | [IAM roles en detalle](08_iam_roles_en_detalle.md) | 60 min | 02 |
| 09 | [Task definition y RunTask](09_task_definition_y_run_task.md) | 45 min | 07, 08 |
| 10 | [Logs y debugging](10_logs_y_debugging.md) | 30 min | 09 |
| 11 | [EventBridge Scheduler](11_eventbridge_scheduler.md) | 45 min | 09 |
| 12 | [Operación diaria](12_operacion_diaria.md) | 60 min | 09, 11 |
| 13 | [Evolución a CI/CD](13_evolucion_a_cicd.md) | 30 min | 12 |
| 14 | [Problemas reales (postmortem)](14_problemas_reales_y_postmortem.md) | 30 min | todos |

**Total:** ~8-10 horas de estudio activo si haces Track A; +4-6 h si agregas Track B.

## Matriz: ¿por dónde empiezo?

| Tu nivel actual | Arranca en | Puedes saltarte |
|---|---|---|
| No he usado AWS nunca | 00 | — |
| Conozco AWS pero no ECS | 00 (setup) → 01 → 06 | 03, 04, 05 si solo quieres el lado compute |
| Ya deployé algo en Fargate antes | 01 | 00, 02 |
| Solo quiero operar lo que ya existe | 12 | módulos de Track B |

## Diagramas

Los diagramas Mermaid se renderizan en GitHub y VSCode (extensión `Mermaid Preview`). El `.mmd` fuente vive en [`diagramas/`](diagramas/) por si quieres editarlos.

## Scripts verificadores

Cada módulo con ejercicios tiene un script `scripts/check_m<NN>_<tema>.sh` que corre inspecciones read-only y reporta pass/fail. Ejemplo:

```bash
bash docs/curso/scripts/check_m03_rds.sh
```

## Convenciones

- Los comandos usan el perfil AWS por defecto (`~/.aws/credentials`). Si usas otro perfil, exporta `AWS_PROFILE=<nombre>` antes.
- Región fija `us-east-1`.
- Cuenta `930067561911`.
- Los sufijos `-curso-<alias>` son para Track B; nunca se colisionan con producción.

## Fuentes de verdad

El curso **explica** la infra; no es la fuente de verdad. Cuando haya duda, consulta:

- `docs/infrastructure/aws_deployment.md` — runbook oficial (inventario + comandos).
- `docs/infrastructure/aws_iam.md` — permisos.
- `docs/decisions.md §8.2.20` — ADR con el "por qué" de ECS Fargate + EventBridge.
- `deploy/taskdef.json`, `deploy/iam/*.json` — configs reales versionadas.
