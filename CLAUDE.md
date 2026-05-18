# CLAUDE.md — MLMonitor

## 1. Identidad del proyecto

MLMonitor es un sistema de monitoreo automatizado **semanal** para el scorecard crediticio **BazBoost V1** (11 segmentos `s1..s11`): calcula drift de variables (PSI), desempeño (Gini/KS), violaciones de ordenamiento y métricas de negocio a partir de datos en base relacional, y publica un PDF con narrativa opcional generada por LLM, enviado por correo y subido a S3.

## 2. Stack y restricciones técnicas

- **Lenguaje / runtime:** Python **3.11** gestionado con **Poetry** (nunca usar `pip` directo). Entorno activo vía `poetry env use /opt/homebrew/bin/python3.11`.
- **Core:** SQLAlchemy **2.0**, Pandas **2.2**, NumPy 1.26, Pydantic Settings, python-dotenv.
- **DB:** **PostgreSQL en RDS** (destino final y ya cableado) / **SQLite** (`mlmonitor_dev.db`) para desarrollo local.
- **LLM:** **AWS Bedrock** con `us.anthropic.claude-haiku-4-5-20251001-v1:0` como default (`config/settings.py::bedrock_model_id`), vía `boto3` con imports perezosos. Override por CLI con `--model-id`.
- **Correo:** **AWS SES** (`src/mlmonitor/email/sender.py::SESEmailSender`).
- **Storage:** **AWS S3** para los PDFs (`src/mlmonitor/storage/s3_uploader.py`); deshabilitado si `S3_BUCKET` está vacío.
- **Secretos:** **AWS Secrets Manager** (credenciales DB y destinatarios de correo); `config/secrets_loader.py` hace fallback a `.env` si no hay AWS disponible.
- **PDF:** **weasyprint 60.2 + pydyf 0.8.0** es la única combinación estable con pip actualmente. No subir a 61.x ni 62.x.
- **Plantillas:** Jinja2 (prompts del LLM y HTML del reporte).
- **Dependencias agrupadas:** grupo `pipeline` (weasyprint, boto3, scipy, jinja2, scikit-learn, matplotlib, python-pptx). Grupo `dev` (pytest, ipykernel).

## 3. Reglas de comportamiento del agente

**Puedes hacer sin pedir permiso:**
- Correr `poetry run python scripts/run_bootstrap.py`, `scripts/run_incremental_etl.py`, `scripts/run_pipeline.py`.
- Correr `poetry run pytest`.
- Editar código en `src/mlmonitor/metrics/`, `pipeline/`, `report/`, `analyst/`, `data/` (ETL), `storage/`, `email/`, `config/`.
- Crear/actualizar tests en `tests/`.

**Pregunta antes de tocar:**
- `src/mlmonitor/db/models.py` — **el modelo de datos es el punto más crítico de la app y está congelado** tras la validación con `notebooks/validacion_metricas_baseline.ipynb`. Cualquier cambio requiere revisión explícita del usuario.
- `DECISIONS.md` (o su futura ubicación `docs/decisions.md`) — ADR que revisa el usuario. Propón el texto, no lo edites solo.
- Añadir/quitar dependencias en `pyproject.toml`.
- Cambios que toquen >3 archivos: muestra el plan primero.

**Nunca hagas:**
- Eliminar archivos en `data/inputs/raw_tables/` sin confirmación explícita.
- Tocar `poetry.lock` manualmente (usa `poetry add/remove/update`).
- `git push` o commits sin que el usuario lo pida.
- Hardcodear variables dependeientes del modelo de datos (como por ejemplo "score_max", "binns", "segmentos 1, segmento 2, etc"). Cualquier variable dependiente del modelo de datos debe ir en el modelo de datos. Si no existe una columna para almacenar algun nuevo valor, crearla (confirmar con el usuario de acuerdo a la "Pregunta antes de tocar")

**Siempre:**
- **Documentar tu trabajo en `devlog.md` (raíz).** Formato: encabezado por fecha ISO + bullets cortos (`- qué hice / qué probé / qué sigue`). Es bitácora viva, no ADR.
- Leer `DECISIONS.md` antes de proponer cambios arquitectónicos — ahí está el "por qué" del proyecto.
- Preguntar cuando dudes; no asumas.

## 4. Convenciones de código

- **Tablas:** `META_*` y `FACT_*` en `UPPER_SNAKE_CASE`. META usa **SCD2** (`valid_from`, `valid_to`, registro activo con `valid_to IS NULL`). FACT es **append-only** con `UniqueConstraint` sobre la clave de negocio para garantizar idempotencia.
- **Columnas y funciones:** `snake_case`. **Clases:** `PascalCase`. **Segmentos:** `s1..s11`.
- **Fechas:** Python `date` alineado al **lunes ISO** (`date.fromisocalendar(y, w, 1)`). El formato `yyyyww` solo se acepta en CSVs de entrada y se convierte inmediatamente.
- **ETL en dos flujos independientes** (ver `data/incremental_etl.py`):
  - **Flow A:** `variables_serc_*.csv` → `FACT_DISTRIBUTIONS` (base de PSI).
  - **Flow B:** `muestra_weekly_*.csv` → `FACT_PERFORMANCE_BINNED` + `FACT_PERFORMANCE_INDIVIDUAL` (base de Gini/KS).
  - No acoplarlos; pueden fallar/correrse independientemente.
- **División ETL vs pipeline (importante para backfill).** El **ETL** trae datos crudos desde CSVs y los deposita en `FACT_DISTRIBUTIONS` y `FACT_PERFORMANCE_*`. El **pipeline** (`run_pipeline.py`) consume esas tablas y deriva métricas (PSI, Gini, KS) que se persisten en `FACT_METRICS_HISTORY`, además de generar PDF + LLM + correo. Razón del split: si cambia una fórmula de métrica, se re-corre solo el pipeline sobre datos crudos existentes — no hace falta re-ingestar. Implicación práctica: un backfill histórico debe correr **ambos** (ETL + pipeline con `--no-email --no-llm`), no solo el ETL. Ver `scripts/backfill.py` y ADR §8.2.21.
- **Madurez:** garantizada por el filtro `origination_week = execution_week - lag` en el ETL. No calcular edades en el código de métricas.
- **Inversión de score:** `inverted = score_max - score` para que score bajo = alto riesgo. `score_max` vive en `MetaModelRegistry` (parametrizable por modelo, no hardcodear).
- **Nulos:** `MISSING_SENTINEL = -100` para variables de scorecard (distinto de `NaN`).
- **Persistencia JSON:** tipo `JSONText` propio en `db/models.py` por compatibilidad SQLite / Postgres / Oracle.
- **Imports perezosos de `boto3`** en módulos AWS (S3, SES, Bedrock, Secrets Manager) — la app debe poder correrse sin AWS.
- **Auto-detección:** el ETL deriva `execution_week` de `MAX(semana_observacion)` del CSV; el pipeline deriva `calculation_date` de `MAX(origination_week)` en DB. Respeta esto en vez de hardcodear fechas.

## 5. Comandos de desarrollo

Todos los comandos se ejecutan desde `mlmonitor/`.

```bash
# Setup del entorno (una sola vez)
poetry env use /opt/homebrew/bin/python3.11
poetry install --with pipeline,dev

# Inicialización única por modelo: crea tablas, carga META y baseline.
# Se requiere --model-id (declara qué modelo se está bootstrapeando); el resto
# de la config (primary_target, segments, variables, etc.) se lee de
# data/inputs/model_configs/<model_id_lowercase>/config.json.
# El baseline se deriva de las primeras N semanas ISO de variables_serc_*.csv
# (ver ADR §8.2.29). Hasta Iteración 2 coexistían bootstrap.py (WIDE legacy)
# y bootstrap_v2.py; D7 los consolidó en un solo run_bootstrap.py.
poetry run python scripts/run_bootstrap.py --model-id BAZBOOST_V1

# ETL semanal — sin --model-id, itera sobre TODOS los modelos activos en
# META_MODEL_REGISTRY (comportamiento default deseado para multi-modelo).
# Auto-detecta la semana si no se pasa --date.
poetry run python scripts/run_incremental_etl.py --date 2026-01-05

# Pipeline: cálculo de métricas + PDF (+ S3 + SES si hay credenciales)
# Mismo patrón de auto-detección plural sobre los modelos activos.
poetry run python scripts/run_pipeline.py --date 2026-01-05
poetry run python scripts/run_pipeline.py --date 2026-01-05 --no-email --no-llm

# Tests
poetry run pytest
poetry run pytest tests/test_psi.py -v

# Reset de DB local (si cambió el schema)
rm mlmonitor_dev.db && poetry run python scripts/run_bootstrap.py --model-id BAZBOOST_V1
```

**Para agregar un modelo nuevo** (ej. `RIESGO_OPERACIONAL_V1`):
1. Crear `data/inputs/model_configs/riesgo_operacional_v1/` con:
   - `config.json` (estructura: ver `bazboost_v1/config.json` como plantilla; campos requeridos en ADR §8.2.30).
   - `variable_descriptions.csv`, `segment_descriptions.csv`, `thresholds.csv`.
2. Versionar los 4 archivos en git (la excepción del `.gitignore` ya está configurada).
3. Re-bootstrap con `--model-id RIESGO_OPERACIONAL_V1`. La DB queda con dos modelos activos.
4. El cron semanal (sin `--model-id`) procesa ambos automáticamente.

Fechas útiles de datos dummy: semana 20 = `2026-01-05` (anomalías visibles), semana 8 = `2025-10-13` (última con outcomes de performance).

## 6. Estado del proyecto (abril 2026)

**Fase:** MVP desplegado en AWS (2026-04-23).

**Lo que ya funciona:**
- Pipeline end-to-end corre en **ECS Fargate** disparado por EventBridge Scheduler los **lunes 08:00 CDMX** (14:00 UTC). Imagen en ECR (`930067561911.dkr.ecr.us-east-1.amazonaws.com/mlmonitor:latest`).
- CSVs semanales se suben a `s3://ml-monitoring-reports-credito/inputs/raw_tables/` y el contenedor hace `aws s3 sync` al arrancar.
- Servicios AWS cableados y consumidos desde la task: RDS Postgres 16, Bedrock Haiku 4.5, SES (sandbox, sender `1206029@onuriscp.com`), Secrets Manager (`ml-monitoring/rds`, `ml-monitoring/SES`), S3.
- Disparo manual: `aws ecs run-task --cluster mlmonitor-cluster --task-definition mlmonitor ...` — ver runbook en `docs/infrastructure/aws_deployment.md §3.3`.
- Tests: 58/58 pasan (verificado con `poetry run pytest`).
- Modelo de datos validado con `notebooks/validacion_metricas_baseline.ipynb`: congelado hasta autorización explícita.

**Decisión arquitectónica formal:** ADR `docs/decisions.md §8.2.20` (ECS Fargate + EventBridge Scheduler). Supersede §8.2.19.

**Deuda técnica priorizada** (detalle en `docs/infrastructure/aws_deployment.md §5`):
1. Cerrar `sg-02e9d008b587402f7` a solo `sg-0c54b54ed399b471c` (hoy abierto `0.0.0.0/0:5432`).
2. Salir de SES sandbox.
3. Escribir Terraform con `terraform import`.
4. CI/CD (GitHub Actions) para build + push a ECR.

**Dudas todavía abiertas:** D6 (refresco del baseline) y D7 (multi-modelo). Ver `dudas_documentacion.md`.
