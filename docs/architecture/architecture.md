# Arquitectura — MLMonitor

Fuente de verdad sobre cómo están organizados los componentes, cómo se comunican y qué servicios externos consumen. Para el "por qué" de cada decisión, ver [`docs/decisions.md`](../decisions.md). Para el schema relacional y sus reglas, ver [`data_model.md`](./data_model.md).

---

## 1. Visión general

MLMonitor es un sistema de monitoreo semanal para scorecards crediticios (hoy solo `BAZBOOST_V1`, 11 segmentos). Ingiere CSVs raw, calcula métricas de drift, desempeño y negocio, y publica un PDF con narrativa opcional de LLM.

Está organizado como **dos unidades lógicas de ejecución** que comparten el mismo schema relacional:

- **ETL** (grupo de deps `main` de Poetry): pobla `META_*` una vez y actualiza las `FACT_*` de insumo cada semana. No requiere servicios AWS ni WeasyPrint.
- **Pipeline** (grupo `pipeline`): lee las `FACT_*` de insumo, calcula las métricas, genera el PDF y lo publica (S3 + SES), consumiendo Bedrock para la narrativa.

La separación existe por dos razones: (1) permitir desplegar cada unidad con sus dependencias mínimas en AWS, y (2) mantener los imports aislados (`mlmonitor.data.*` + `mlmonitor.db.*` para el ETL; `mlmonitor.pipeline.*` / `metrics.*` / `report.*` / `analyst.*` / `storage.*` / `email.*` para el Pipeline). Fue pensada originalmente para un deploy híbrido VM + Cloud (decisions §8.2.15), pero ese modelo fue descartado (§8.2.19): hoy todo corre desde local contra AWS y está pendiente de migrarse completamente a AWS.

---

## 2. Diagrama de componentes

```
                         ┌─────────────────────────────────────┐
                         │ CSVs raw en data/inputs/raw_tables/ │
                         │  - base_train_test_bb.csv (WIDE)    │
                         │  - variables_serc_YYYYWW.csv (LONG) │
                         │  - muestra_weekly_YYYYWW.csv        │
                         └─────────────┬───────────────────────┘
                                       │
                     ┌─────────────────┴─────────────────┐
                     │                                   │
                     ▼                                   ▼
         ┌──────────────────────┐            ┌──────────────────────┐
         │  scripts/            │            │  scripts/            │
         │  run_bootstrap.py    │            │  run_incremental_    │
         │  (una vez)           │            │  etl.py (semanal)    │
         └──────────┬───────────┘            └──────────┬───────────┘
                    │                                   │
                    ▼                                   ▼
         ┌──────────────────────┐            ┌──────────────────────┐
         │  data/bootstrap.py   │            │  data/incremental_   │
         │  ModelBootstrap      │            │  etl.py IncrementalETL│
         │                      │            │  Flow A + Flow B     │
         └──────────┬───────────┘            └──────────┬───────────┘
                    │                                   │
                    ▼                                   ▼
         ┌──────────────────────────────────────────────────────────┐
         │                Base de datos relacional                  │
         │     RDS PostgreSQL (prod)  /  SQLite dev (mlmonitor_dev) │
         │  META_MODEL_REGISTRY · META_VARIABLES · META_METRIC_     │
         │  THRESHOLDS · META_BASELINE_DISTRIBUTIONS                │
         │  FACT_DISTRIBUTIONS · FACT_PERFORMANCE_BINNED ·          │
         │  FACT_PERFORMANCE_INDIVIDUAL · FACT_METRICS_HISTORY      │
         └──────────────────────────┬───────────────────────────────┘
                                    │
                                    ▼
                       ┌────────────────────────┐
                       │  scripts/run_pipeline.py│
                       └────────────┬────────────┘
                                    │
                                    ▼
                       ┌────────────────────────┐
                       │ pipeline/orchestrator  │
                       │ PipelineOrchestrator   │
                       └────────────┬────────────┘
                                    │
             ┌──────────────┬───────┴────────┬──────────────────┐
             ▼              ▼                ▼                  ▼
  ┌───────────────┐ ┌───────────────┐ ┌──────────────┐ ┌──────────────────┐
  │ metrics/      │ │ report/       │ │ storage/     │ │ email/           │
  │ calculator    │ │ builder +     │ │ s3_uploader  │ │ sender           │
  │ +psi,         │ │ renderer      │ │              │ │ (SESEmailSender) │
  │ performance,  │ │ (Jinja2 +     │ │              │ │                  │
  │ business      │ │ WeasyPrint)   │ │              │ │                  │
  └───────┬───────┘ └───────┬───────┘ └──────┬───────┘ └──────────┬───────┘
          │                 │                │                    │
          ▼                 ▼                ▼                    ▼
  FACT_METRICS       analyst/           AWS S3                AWS SES
  _HISTORY           bedrock_analyst    (s3://bucket/         (send_raw_email)
                           │            mlmonitor/reports/…)
                           ▼
                     AWS Bedrock
                     (Claude Haiku 4.5)
```

Notas del diagrama:

- El bloque "Base de datos" es único; ETL y Pipeline conviven sobre la misma BD (distinta instancia por entorno).
- `FACT_METRICS_HISTORY` es la tabla que escribe el Pipeline, no el ETL.
- `S3` y `SES` son pasos opcionales: si `S3_BUCKET` está vacío o `--no-email` se pasa, se skipean.

---

## 3. Componentes principales

### 3.1 ETL (`src/mlmonitor/data/`)

#### `bootstrap.py` · `ModelBootstrap`

Se ejecuta **una sola vez por modelo**. Crea los registros SCD2 de `META_*` y pobla `META_BASELINE_DISTRIBUTIONS` leyendo `base_train_test_bb.csv` (formato WIDE). Los bin edges numéricos salen de cuantiles del baseline; el score usa bins fijos de 100 puntos (`SCORE_BINS`).

Constantes clave:

- `MODEL_ID = "BAZBOOST_V1"`
- `MISSING_SENTINEL = -100` (valor reservado para nulos en variables numéricas)
- `TARGET_VARIABLES`: 5 targets con sus `lag_semanas` (2, 4, 6, 8, 16).

#### `incremental_etl.py` · `IncrementalETL`

Se ejecuta **semanalmente**. Dos flujos independientes:

- **Flow A — Distribuciones:** toma `variables_serc_*.csv` (LONG, una fila por solicitud × variable), mapea nombres SERC → canónicos (`variable_mapping.py`), filtra por la `execution_week` y escribe en `FACT_DISTRIBUTIONS` (un bin_label por variable × semana × segmento). Este flujo alimenta el cálculo de PSI y null_rate.
- **Flow B — Performance:** toma `muestra_weekly_*.csv` (formato WIDE, una fila por crédito × semana), filtra créditos con `flg_baz_boost=1 AND flg_surtida=1` y, **por cada target**, selecciona la cohorte madurada (`semana_num = iso_week(execution_week - lag)`). Escribe en `FACT_PERFORMANCE_BINNED` (agregado por score_bin) y `FACT_PERFORMANCE_INDIVIDUAL` (una fila por crédito × ventana).

Auto-detección: si no se pasa `--date`, la semana se deriva de `MAX(semana_observacion)` en `muestra_weekly`. Las fechas se alinean al **Lunes ISO** (`date.fromisocalendar(y, w, 1)`); ver §8.2.12 de decisions para el bug histórico W-MON vs ISO-MON.

Idempotencia: antes de insertar cada (segmento, semana, target) se hace un `SELECT 1 … LIMIT 1`. Re-ejecutar no duplica.

Flow A y Flow B se ejecutan **secuencialmente** en la misma invocación. Podrían paralelizarse (son independientes), pero el overhead actual es <1 min y no justifica la complejidad adicional.

#### `variable_mapping.py`

Catálogo estático que traduce nombres SERC a los canónicos usados en el modelo. Expone:

- `CANONICAL_VARIABLES[segment_id] → list[var_name]`
- `SERC_TO_CANONICAL[serc_name] → canonical_name`
- `SEGMENT_GROUP_NAMES` (G1–G5 agrupan los segmentos).
- `SEGMENT_FEATURE_COUNTS` (conteo de features por segmento).

`fisexo` es la única variable categórica del modelo actual (hardcodeado en bootstrap). Cambio requiere revisitar el código.

### 3.2 Pipeline (`src/mlmonitor/pipeline/`, `metrics/`, `report/`, `analyst/`)

#### `pipeline/orchestrator.py` · `PipelineOrchestrator.run()`

Orquesta los 4 pasos del reporte semanal:

1. **Métricas (`metrics/calculator.py`):** para cada segmento calcula PSI por variable + PSI máximo del segmento, null_rate por variable, y por cada target Gini, KS y `ordering_violations`. Cada métrica se evalúa contra su umbral en `META_METRIC_THRESHOLDS` (alertas OK/WARNING/CRITICAL) y se persiste en `FACT_METRICS_HISTORY`.
2. **Construcción de reporte (`report/builder.py`):** consulta `FACT_METRICS_HISTORY` de la semana, arma el `AnalysisContext` con segmentos ordenados por urgencia y `fleet_summary` agregado. Si hay analista, invoca al LLM para generar narrativa por flota y por segmento.
3. **Render PDF (`report/renderer.py`):** renderiza `templates/fleet_report.html` con Jinja2 (filtro custom `_nl2br`) y lo convierte a PDF con WeasyPrint 60.2 + pydyf 0.8.0. Si WeasyPrint no está disponible, cae en HTML.
4. **Publicación:** sube el PDF a S3 (`storage/s3_uploader.py`) si `S3_BUCKET` está configurado, y lo envía por SES (`email/sender.py::SESEmailSender`) a los destinatarios en `settings.recipient_list` salvo que se pase `--no-email`.

La `calculation_date` se auto-detecta como `MAX(FACT_DISTRIBUTIONS.origination_week)` si no se pasa `--date`. El orchestrator también calcula `data_lag_weeks` vs el lunes de la semana actual del calendario.

#### `metrics/`

- **`psi.py`:** PSI por variable comparando `FACT_DISTRIBUTIONS` de la semana vs `META_BASELINE_DISTRIBUTIONS` (entrenamiento), con `EPS=1e-8` para evitar `log(0)`. Umbrales por defecto: `<0.10` OK, `0.10–0.20` WARNING, `>0.20` CRITICAL.
- **`performance.py`:** Gini/KS desde `FACT_PERFORMANCE_INDIVIDUAL` (nivel crédito, sin error de discretización). Fallback a `FACT_PERFORMANCE_BINNED` si no hay datos individuales. Invierte el score con `inverted = score_max - fnpuntaje` tomando `score_max` de `MetaModelRegistry`.
- **`business_metrics.py`:** tabla de tasas por decil de score y detección de violaciones de monotonía. Cada target tiene su propia `origination_week = calculation_week - lag`.

#### `report/`

- **`builder.py`:** ensambla el `AnalysisContext` (flota + segmentos + coverage por target) y opcionalmente pide al `Analyst` la narrativa.
- **`renderer.py`:** `PDFRenderer` con entorno Jinja2 y filtro `_nl2br` para preservar saltos de línea del LLM.
- **`templates/fleet_report.html`** + **`submodel_section.html`** + **`styles.css`**.

#### `analyst/`

- **`base.py`:** dataclasses (`SegmentMetrics`, `AnalysisContext`, `AnalysisResult`).
- **`bedrock_analyst.py`:** `BedrockAnalyst` vía `boto3.client("bedrock-runtime")`. Usa `anthropic_version="bedrock-2023-05-31"`, `max_tokens=2048`, `temperature=0.2`. Dos prompts Jinja: uno para el resumen de flota, otro por segmento.
- **`__init__.py`::`create_analyst(**kwargs)`** es la factory. Hoy siempre retorna `BedrockAnalyst`.

### 3.3 Infraestructura común (`src/mlmonitor/db/`, `config/`, `storage/`, `email/`)

- **`db/models.py`:** los 8 modelos SQLAlchemy 2.0 (4 META + 4 FACT) con SCD2, append-only y `JSONText` custom para portabilidad SQLite/Postgres. Ver [`data_model.md`](./data_model.md) para detalle.
- **`db/connection.py`::`create_db_engine(db_url)`:** detecta dialecto por prefijo y configura `connect_args` apropiados.
- **`db/session.py`::`get_session(engine)`:** context manager con commit/rollback automáticos.
- **`config/settings.py`::`Settings`:** Pydantic Settings. Carga `.env` + overrides de Secrets Manager mediante `_build_settings()`.
- **`config/secrets_loader.py`::`load_all_secrets(region)`:** lee `ml-monitoring/rds` y `ml-monitoring/SES`. `boto3` se importa lazy; si falla (ETL sin AWS), `_build_settings` lo captura y usa defaults. Ver [`../infrastructure/aws_secrets_manager.md`](../infrastructure/aws_secrets_manager.md).
- **`storage/s3_uploader.py`::`S3Uploader.from_settings()`:** sube PDFs a `s3://{S3_BUCKET}/{S3_PREFIX}/{filename}` con `ContentType=application/pdf`.
- **`email/sender.py`::`SESEmailSender.from_settings()`:** arma `MIMEMultipart` (HTML body + PDF adjunto) y llama `ses.send_raw_email`.

---

## 4. Entry points CLI

Todos se ejecutan desde `mlmonitor/` con `poetry run python scripts/...`.

| Script | Rol | Args principales |
|---|---|---|
| `scripts/run_bootstrap.py` | Inicialización única: crea tablas, pobla META y baseline. | `--db-url`, `--raw-dir`, `--baseline-file` |
| `scripts/run_incremental_etl.py` | ETL semanal. Auto-detecta semana si no se pasa `--date`. | `--date`, `--db-url`, `--raw-dir`, `--model-id`, `--serc-file`, `--weekly-file` |
| `scripts/run_pipeline.py` | Métricas + PDF + S3 + SES. | `--date`, `--db-url`, `--no-email`, `--no-llm`, `--model-id` |
| `scripts/create_presentation.py` / `create_cml_presentation.py` | Generadores PPTX auxiliares (no forman parte del flujo semanal principal). | — |
| `scripts/export_rds_samples.py` | Utilería para exportar muestras desde RDS. | — |

---

## 5. Fuentes de datos

| Archivo | Formato | Qué contiene | Lo lee |
|---|---|---|---|
| `data/inputs/raw_tables/base_train_test_bb.csv` | WIDE | ~501K créditos del entrenamiento con variables canónicas, score y 5 targets históricos. Es la referencia de PSI. | `bootstrap.py` |
| `data/inputs/raw_tables/variables_serc_YYYYWW.csv` | LONG | Una fila por (solicitud, variable) scoreada en la semana W. | `incremental_etl.py` (Flow A) |
| `data/inputs/raw_tables/muestra_weekly_YYYYWW.csv` | WIDE (tabular) | Una fila por crédito × semana con score y los 5 targets calculados. | `incremental_etl.py` (Flow B) |

Convenciones de fecha y filtrado: ver [`data_model.md §0`](./data_model.md#0-datos-raw-y-contexto-de-negocio) (columnas, filtros `flg_baz_boost=1 AND flg_surtida=1`, lógica de madurez).

---

## 6. Servicios externos (AWS)

Todos usan `boto3` con **lazy import** (`import boto3` dentro de la función que lo usa) para que el ETL puro pueda correr sin las dependencias del grupo `pipeline`.

| Servicio | Uso | Env / Setting | Cómo se autentica |
|---|---|---|---|
| **RDS PostgreSQL** | BD relacional (prod). En dev se usa SQLite `mlmonitor_dev.db`. | `DB_URL` compuesto desde secreto `ml-monitoring/rds` | Credenciales en Secrets Manager |
| **Bedrock (Claude Haiku 4.5)** | Narrativa del PDF. Modelo `us.anthropic.claude-haiku-4-5-20251001-v1:0`. | `BEDROCK_MODEL_ID`, `AWS_REGION` | IAM role / AWS CLI profile |
| **S3** | Almacenamiento de los PDFs generados. | `S3_BUCKET`, `S3_PREFIX` (default `mlmonitor/reports`). Vacío = upload deshabilitado. | IAM role / AWS CLI profile |
| **SES** | Envío del PDF por correo. | `SES_FROM_EMAIL`, `EMAIL_FROM`, `EMAIL_RECIPIENTS` (o `ml-monitoring/SES`). Vacío = envío deshabilitado. | IAM role / AWS CLI profile |
| **Secrets Manager** | Origen de credenciales DB y de config SES. | Secretos `ml-monitoring/rds` y `ml-monitoring/SES`. | IAM role / AWS CLI profile |

Si Secrets Manager no está disponible, `_build_settings()` captura la excepción y usa defaults del `.env` — útil para desarrollo local sin AWS. La plataforma AWS concreta donde correrá el flujo (ECS, Step Functions, Batch, …) está pendiente de decidirse (ver `dudas_documentacion.md` D4).

---

## 7. Flujo semanal en producción (MVP AWS, desde 2026-04-23)

1. **Antes del lunes:** el usuario sube los CSVs de la semana a `s3://ml-monitoring-reports-credito/inputs/raw_tables/` (`variables_serc_<...>.csv` y `muestra_weekly_<...>.csv`).
2. **Lunes 08:00 CDMX (14:00 UTC):** EventBridge Scheduler `mlmonitor-weekly` dispara una task de ECS Fargate sobre `mlmonitor-cluster` con la task definition `mlmonitor` (ver [`../infrastructure/aws_deployment.md`](../infrastructure/aws_deployment.md) §1).
3. **Dentro del contenedor** (`docker/entrypoint.sh`):
   a. `aws s3 sync` de `inputs/raw_tables/` al filesystem local.
   b. `run_incremental_etl.py` (auto-detecta la semana desde `MAX(semana_observacion)`).
   c. `run_pipeline.py` (métricas → PDF → S3 `mlmonitor/reports/` → SES al destinatario verificado).
4. Logs en CloudWatch (`/ecs/mlmonitor`, retención 30 días).
5. Disparo manual siempre disponible con `aws ecs run-task` directo — no requiere deshabilitar el schedule.

Plataforma y decisión formal: [`../decisions.md` §8.2.20](../decisions.md). Runbooks operativos (disparo manual, lectura de logs, rollback, promoción de imagen): [`../infrastructure/aws_deployment.md §3`](../infrastructure/aws_deployment.md).

---

## 8. Dependencias críticas

- **Python 3.11** (Poetry env). Nunca `pip` directo.
- **WeasyPrint 60.2 + pydyf 0.8.0**: combinación pinneada. Versiones 61.2 y 62.x no son compatibles con las versiones disponibles de pydyf en PyPI y rompen el render.
- **SQLAlchemy 2.0**, **Pandas 2.2**, **NumPy 1.26**, **Pydantic Settings**, **python-dotenv**: core.
- **boto3 ^1.34**, **scipy**, **scikit-learn**, **matplotlib**, **Jinja2**, **python-pptx**: grupo `pipeline` (opcional).
- **psycopg2-binary** está en el core para poder conectar RDS desde el ETL también.
- **python-oracledb** queda como `extras = ["oracle"]`: histórico, la BD productiva es Postgres no Oracle, pero el schema soporta ambos por el tipo `JSONText`.

---

## 9. Qué **no** está todavía resuelto

- Política de refresco del baseline de entrenamiento (duda D6).
- Soporte multi-modelo en paralelo (duda D7).
- Deuda técnica del deploy AWS: SG de RDS aún abierto a `0.0.0.0/0`, SES en sandbox, Terraform sin escribir, CI/CD pendiente, RDS en subnets públicas. Detalle en [`../infrastructure/aws_deployment.md §5`](../infrastructure/aws_deployment.md).

Las dudas D1 (nombre de secreto SES), D2 (destinatarios), D3 (bucket), D4 (plataforma), D5 (origen de CSVs) y D8 (SLA) quedaron resueltas y aplicadas el 2026-04-23 — ver [`../../dudas_documentacion.md`](../../dudas_documentacion.md).
