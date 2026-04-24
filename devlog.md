# devlog.md — Bitácora del agente

Bitácora viva de las sesiones de trabajo del agente sobre MLMonitor. No es ADR: aquí van "qué hice / qué probé / qué sigue" en formato corto. Para decisiones arquitectónicas formales ver [`docs/decisions.md`](docs/decisions.md).

Formato: encabezado por fecha ISO (`## YYYY-MM-DD`) + bullets cortos. Entradas más recientes **arriba**.

---

## 2026-04-23 (tarde — curso de AWS deployment)

- Creada carpeta `docs/curso/` con material didáctico (15 módulos + README + scripts verificadores + diagramas Mermaid + sandbox/teardown).
- Track A (inspección read-only) + Track B (recrear con sufijos `-curso-<alias>`).
- Módulo 12 responde 4 dudas operativas del usuario: re-ejecutar semana X, backfill histórico, push de cambios de código, cambios a tablas META (SCD2).
- Módulo 14 documenta 4 incidentes reales del deploy: pg_dump v14 vs RDS 16, libgdk-pixbuf rename, Docker Hub 503, SES AccessDenied en recipient.
- Siguiente posible iteración: ADR §8.2.21 para soportar `RUN_DATE` env var en `entrypoint.sh` (mejora la Opción B del módulo 12). Requiere aprobación del usuario.

## 2026-04-23

- **Migración del MVP a AWS completada.** El pipeline ahora corre en ECS Fargate, disparado manualmente con `aws ecs run-task` y semanalmente con EventBridge Scheduler (lunes 08:00 CDMX = 14:00 UTC).
- **F0 — Reset de RDS y smoke test local:**
  - `pg_dump` de la DB existente → `data/backups/rds_pre_reset_2026-04-23.sql` (987 KB).
  - `DROP TABLE CASCADE` de las 9 tablas (incluía `FACT_PERFORMANCE_OUTCOMES` no documentada; revisar si es legado o activa — ver deuda abajo).
  - `run_bootstrap.py` + `run_incremental_etl.py` + `run_pipeline.py` contra RDS desde local: PDF en S3 + correo SES entregados.
  - Subida de los 3 CSVs a `s3://ml-monitoring-reports-credito/inputs/raw_tables/` (~1 GB total).
- **F1 — Contenedor:** `mlmonitor/Dockerfile` (python:3.11-slim + libs nativas WeasyPrint + AWS CLI v2 + Poetry), `mlmonitor/docker/entrypoint.sh` (sync S3 → ETL → Pipeline), `mlmonitor/.dockerignore`. Ajuste necesario: Debian bookworm renombró `libgdk-pixbuf2.0-0` a `libgdk-pixbuf-2.0-0`. Imagen validada con `docker run` local contra RDS.
- **F2 — ECR:** repo `mlmonitor` creado. Build `linux/amd64` con `docker buildx` y push de tags `v0.1.0` + `latest` (~660 MB).
- **F3 — IAM:** roles `mlmonitor-ecs-execution` (managed `AmazonECSTaskExecutionRolePolicy`) y `mlmonitor-task` (inline policy con Secrets, Bedrock, S3 read inputs, S3 write reports, SES send). Policies JSON commiteadas en `mlmonitor/deploy/iam/`.
- **F4 — SG:** `mlmonitor-fargate-sg` (`sg-0c54b54ed399b471c`) en VPC default, solo egress. **Deuda:** el SG de RDS `sg-02e9d008b587402f7` sigue abierto 5432 a `0.0.0.0/0`; cerrarlo al SG de Fargate en próxima iteración.
- **F5 — CloudWatch:** log group `/ecs/mlmonitor`, retención 30 días.
- **F6 — ECS:** cluster `mlmonitor-cluster`, task definition `mlmonitor:1` (cpu 1024 / memory 4096, runtime X86_64, envs S3/Bedrock/INPUTS, logs a CloudWatch). JSON en `mlmonitor/deploy/taskdef.json`.
- **F7 — Smoke test ECS:** dos corridas. La primera falló en SES (`AccessDenied` sobre la identity del **destinatario**, no del sender — SES exige ambas o Condition `ses:FromAddress`). Policy actualizada para incluir ambas identities + Condition. Segunda corrida: exit 0, PDF en S3, correo entregado. Tiempo ~3:25.
- **F8 — Scheduler:** rol `mlmonitor-scheduler-invoke` y schedule `mlmonitor-weekly` con cron `0 14 ? * MON *` UTC, estado `ENABLED`. Target JSON en `mlmonitor/deploy/scheduler-target.json`. Disparo manual sigue disponible con `aws ecs run-task` directo.
- **F9 — Documentación:** ver cambios en `docs/decisions.md` (§8.2.20), nuevo `docs/infrastructure/aws_deployment.md`, cierre de dudas D1/D2/D3/D4/D5/D8 en `dudas_documentacion.md`, actualización de `CLAUDE.md §6` y `docs/architecture/architecture.md` §6/§7/§9. Archivo `docs/handoff_aws_deployment.md` eliminado al cerrar F9.
- **Deuda técnica registrada:**
  - `sg-02e9d008b587402f7` abierto 5432 a `0.0.0.0/0` — cerrarlo al SG de Fargate.
  - SES sigue en sandbox — abrir ticket para salir.
  - Terraform aún sin escribir — siguiente paso tras estabilizar MVP (usar `terraform import`).
  - CI/CD (GitHub Actions) para build + push a ECR pendiente.
  - `FACT_PERFORMANCE_OUTCOMES` aparece en RDS pero no en `db/models.py` documentado — validar si es tabla viva o residuo.
  - RDS `PubliclyAccessible=true` — mover a subnets privadas + NAT en fase de hardening.
  - D6 (refresco de baseline) y D7 (multi-modelo) siguen abiertas; no bloquean MVP.
- **Qué sigue:**
  - Subir a S3 el CSV real de la próxima semana productiva cuando toque y dejar que el Scheduler dispare solo el lunes.
  - Escribir Terraform importando lo ya creado.
  - Cerrar deuda de seguridad (SG de RDS + sandbox SES).

---

## 2026-04-22

- Limpieza de `docs/decisions.md` para dejarlo como ADR pura:
  - Eliminado §8.1 "Contexto del negocio y la data" → migrado a `docs/architecture/data_model.md` como nueva **§0 "Datos raw y contexto de negocio"** (targets/lags, columnas de `variables_serc` y `muestra_weekly`, filtros ETL, convención de fechas, flujo semanal).
  - Eliminado §8.3 "TODOs" → pendientes accionables migrados a nuevo **`docs/backlog.md`** (5 items: índice compuesto FACT_PERFORMANCE_INDIVIDUAL, tests de bootstrap/ETL, FACT_METRICS_HISTORY BI-friendly, desacoplar metric_id de SCD2, CI ambos perfiles). Observaciones de diseño movidas a `data_model.md` (score bins fijos, `fisexo` categórica, `b_malo8_16` en dev) y `architecture.md` (secuencialidad Flow A/B).
  - Eliminado §8.4 "Estado actual" entero (status + tablas de changelog por iteración): es bitácora/git log, no ADR.
  - Trimado §8.2.16 (archivo baseline) y §8.2.17 (notebook validación) a sus decisiones mínimas; detalle descriptivo movido a `data_model.md §2.4` (sub-sección "Estructura del CSV fuente") y nuevo **`notebooks/README.md`**.
  - Reordenado §8.2.19 para que siga a §8.2.18 (antes estaba al final del archivo, tras §8.4).
  - Resultado: `decisions.md` pasó de 553 líneas a ~210 y solo contiene ADRs numeradas.
- Split de `docs/infrastructure/aws_secrets_manager.md §4` (permisos IAM mínimos): narrow al scope del archivo (solo `secretsmanager:GetSecretValue`) y creación de **`docs/infrastructure/aws_iam.md`** con la matriz IAM cross-service (SM, Bedrock, S3, SES) diferenciando rol Pipeline vs rol ETL.
- Cross-refs actualizadas: `data_model.md` (§2.3, §3.3, §3.4 ahora apuntan a `backlog.md`), `architecture.md §5` (ahora a `data_model.md §0`), `CLAUDE.md` ("verificado con `poetry run pytest`" en vez de "ver DECISIONS.md §8.4").
- Nota operativa añadida a `architecture.md §3.1`: Flow A + Flow B corren secuencialmente, podrían paralelizarse pero el overhead <1 min no lo justifica.
- Qué sigue:
  - Responder las dudas D1..D8 para poder formalizar `aws_iam.md` y cerrar `architecture.md §9`.
  - Cuando haya CI, convertir los items 2 y 5 de `backlog.md` en PRs concretos.
  - Registrar en `backlog.md` cualquier nueva deuda técnica que aparezca — no volver a mezclarla con `decisions.md`.

---

## 2026-04-20

- Creé la estructura inicial de documentación del proyecto (no había `CLAUDE.md` ni carpeta `docs/`):
  - `CLAUDE.md` en raíz: identidad, stack, reglas de autonomía, convenciones, comandos, estado. Luego corregí dos imprecisiones iniciales: el default de Bedrock es Haiku 4.5 (`us.anthropic.claude-haiku-4-5-20251001-v1:0`), no Sonnet; y los tests son 58/58, no 40/41.
  - `docs/decisions.md`: copié el contenido de `DECISIONS.md` raíz y añadí la ADR §8.2.19 que documenta el descarte de la arquitectura VM+Cloud (supersede §8.2.15) — ahora todo corre en AWS desde local, pendiente de migrar la ejecución completa.
  - `DECISIONS.md` raíz ahora es stub de redirección a `docs/decisions.md`.
  - `docs/architecture/architecture.md`: componentes, diagrama textual, entry points CLI, servicios AWS, flujo semanal.
  - `docs/architecture/data_model.md`: reglas transversales (SCD2, append-only, JSONText, Lunes ISO, `origination_week` con dos semánticas) + detalle de las 8 tablas y reglas de negocio.
  - `docs/infrastructure/aws_secrets_manager.md`: inventario de `ml-monitoring/rds` y `ml-monitoring/SES`, precedencia de config, permisos IAM mínimos.
  - `dudas_documentacion.md`: archivo vivo con 8 dudas abiertas (nombre exacto del secreto SES, destinatarios, bucket S3 definitivo, plataforma AWS, origen de CSVs raw, refresco del baseline, multi-modelo, SLA).
- Verificaciones: `poetry run pytest --co -q` reporta 58 tests; `config/settings.py` confirma Bedrock Haiku 4.5 como default.
- Qué sigue:
  - Pedir al usuario que revise `dudas_documentacion.md` y resuelva los bloqueos.
  - Una vez resueltas D4, D5 y D8, ampliar `architecture.md` con el diagrama de ejecución en AWS (plataforma + disparador + origen de CSVs).
  - Evaluar si `CLAUDE.md §6` debe actualizarse cuando la migración AWS arranque.
  - Cuando se modifique algo del schema, registrarlo como nueva `§8.2.x` en `docs/decisions.md` y replicar acá el resumen.
