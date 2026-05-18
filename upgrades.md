# upgrades.md — Mejoras propuestas para MLMonitor

> Documento vivo de **mejoras técnicas y de modelo de datos** propuestas tras una auditoría completa del repo (2026-05-06). No es backlog operativo (ver [`docs/backlog.md`](docs/backlog.md)) ni ADR (ver [`docs/decisions.md`](docs/decisions.md)). Cada item describe el problema concreto, su impacto, una propuesta accionable y la prioridad.
>
> El criterio de evaluación es **doble**:
> 1. **Multi-modelo / multi-area:** la herramienta se llevará a otras áreas con otros modelos. Lo que hoy es BAZBOOST_V1 mañana será BAZBOOST_V2, ELEKTRA_RIESGO_X, etc. La auditoría va sobre qué se desacopla bien y qué no.
> 2. **No explotar en complejidad:** un solo desarrollador. Las propuestas privilegian cambios incrementales, alta-señal y bajo-mantenimiento sobre arquitectura especulativa.
>
> **Convención de prioridades:**
> - **P0** — bloquea el siguiente modelo o introduce riesgo de bug silencioso. Hay que hacerlo antes de incorporar el segundo modelo.
> - **P1** — alta deuda; debilita la operación pero el sistema sigue funcionando. Hacer en 1–2 sprints.
> - **P2** — mejora claramente identificable, accionable, no urgente.
> - **P3** — nice-to-have; depende de feedback real para confirmar valor.

---

## A. Modelo de datos y multi-modelo

Es la categoría con más impacto cuando la herramienta se extienda a otras áreas/modelos. Hoy `MetaModelRegistry` ya lo soporta en schema, pero el código asume implícitamente que sólo hay `BAZBOOST_V1`.

### A1. Eliminar `MODEL_ID = "BAZBOOST_V1"` hardcodeado en código de runtime — **P0**

**Dónde está el hardcode:**
- `src/mlmonitor/pipeline/orchestrator.py:25` → `MODEL_ID = "BAZBOOST_V1"`
- `src/mlmonitor/data/incremental_etl.py:47` → `def __init__(self, session, model_id: str = "BAZBOOST_V1")`
- `src/mlmonitor/data/bootstrap.py:39` → `MODEL_ID = "BAZBOOST_V1"` (constante del módulo)
- `scripts/run_incremental_etl.py:87` y `scripts/run_pipeline.py:52` → `default="BAZBOOST_V1"` en argparse

**Problema:** cuando llegue el segundo modelo no es claro cómo coexisten en el mismo run. El default a `BAZBOOST_V1` esconde la decisión de qué se está monitoreando.

**Propuesta:**
1. Eliminar el default `BAZBOOST_V1` en argparse y volverlo `required=True` en `run_incremental_etl.py` y `run_pipeline.py`. (O leer de env var `MODEL_ID` con fallback claro.)
2. Crear un comando `run_pipeline_for_all_active_models.py` (o flag `--model-id all`) que itere sobre todos los `model_id` activos en `META_MODEL_REGISTRY`. La task de ECS pasaría a invocar ese comando — un solo schedule mensual procesa toda la flota multi-modelo.
3. Eliminar la constante `MODEL_ID` global de `bootstrap.py` y `orchestrator.py`. Donde haga falta el "modelo por defecto" para tests, leerlo de `MetaModelRegistry`.

**Impacto multi-modelo:** alto. Es el bloqueante real de "agregar el siguiente modelo".

---

### A2. `PRIMARY_TARGET = "b_malo14_26"` debe vivir en `META_MODEL_REGISTRY` — **P0**

**Dónde está el hardcode:**
- `src/mlmonitor/data/bootstrap.py:60` → `PRIMARY_TARGET = "b_malo14_26"`
- `src/mlmonitor/report/builder.py:19` → `from mlmonitor.data.bootstrap import PRIMARY_TARGET`
- `src/mlmonitor/analyst/base.py:59` → `primary_target: str = "b_malo8_13"` (¡desincronizado!)
- `src/mlmonitor/report/templates/fleet_report.html:14` → vía `context.primary_target`

**Problema crítico:** ya hay desincronización entre `bootstrap.PRIMARY_TARGET = "b_malo14_26"` y el default del dataclass `AnalysisContext.primary_target = "b_malo8_13"`. Es el ejemplo libro de texto de "valor que depende del modelo de datos pero vive en código". Cada modelo nuevo tendrá un target primario distinto y olvidarse de cambiar la constante es un bug inevitable.

**Propuesta de schema (cambio mínimo):**
1. Agregar columna `primary_target_variable` a `META_MODEL_REGISTRY` (FK opcional a `META_VARIABLES.id` o string con el `variable_name`; recomiendo string porque la SCD2 de variables le rompería la FK al cerrar registros).
   ```python
   # db/models.py::MetaModelRegistry
   primary_target_variable = Column(String(100), nullable=True)  # nombre del target primario (ej: "b_malo14_26")
   ```
2. `bootstrap.py::_populate_meta_model_registry` lo pobla con el valor que hoy está en `PRIMARY_TARGET`.
3. `report/builder.py` lo lee de la primera fila de `model_regs` en lugar de importar `PRIMARY_TARGET`. Si es NULL, hace fallback a "target con lag mediano" (lógica que ya existe).
4. Eliminar la constante `PRIMARY_TARGET` de `bootstrap.py` (queda sólo como dato del bootstrap inicial, no como import).
5. Eliminar el default `"b_malo8_13"` del dataclass.

**Beneficio:** el target primario por modelo se vuelve metadata explícita en DB. Para añadir un modelo nuevo basta inicializarlo con su primario; no hay que tocar código.

**Impacto multi-modelo:** alto. Junto con A1 es el cambio que viabiliza incorporar otro modelo sin tocar lógica.

---

### A3. Reglas de agregación de severidad como configuración por modelo, no global — **P1**

**Dónde está el hardcode:**
- `config/settings.py:32-34`:
  ```python
  status_crit_count_to_critical: int = 8
  status_crit_count_to_warning: int = 5
  status_warn_count_to_warning: int = 8
  ```

**Tu duda explícita:** "¿es el mejor lugar para esto?" — **No**. Tres razones:

1. **Es configuración de negocio, no de runtime.** Un modelo de fraude probablemente quiere reglas distintas (más estrictas) que un scorecard de crédito. Settings.py es para infra (DB URL, AWS region, paths); no debería contener parámetros de evaluación de salud.
2. **No es versionable ni auditable.** Si cambias el umbral de 5→8 (como pasó el 2026-05-05 según `devlog.md`), no queda registro en DB de cuándo y por qué — la regla `status_es` aplicada a métricas históricas se vuelve no reproducible.
3. **No escala a multi-modelo.** Settings.py es global; un cambio impacta a todos los modelos.

**Propuesta:**
1. Crear nueva tabla `META_AGGREGATION_RULES` (SCD2) o ampliar `META_MODEL_REGISTRY`. Recomiendo tabla nueva por dos razones: (a) las reglas son versionadas independientemente del modelo, (b) son N parámetros, no uno solo.
   ```python
   class MetaAggregationRules(Base):
       __tablename__ = "META_AGGREGATION_RULES"
       id = Column(Integer, primary_key=True, autoincrement=True)
       model_registry_id = Column(Integer, ForeignKey("META_MODEL_REGISTRY.id"), nullable=True)  # NULL = global
       rule_name = Column(String(100), nullable=False)  # ej: "status_crit_count_to_critical"
       rule_value = Column(Float, nullable=False)
       valid_from = Column(Date, nullable=False)
       valid_to = Column(Date, nullable=True)
       __table_args__ = (UniqueConstraint("model_registry_id", "rule_name", "valid_from"),)
   ```
2. `_aggregate_status` recibe el dict de reglas resuelto (specific → global → default).
3. `bootstrap` siembra los valores actuales con `valid_from = date.today()`.
4. Borrar las 3 propiedades de `Settings` (o dejar `default_*` como fallback in-code para evitar que el sistema explote si la tabla está vacía).

**Beneficio extra:** fanout natural a otros parámetros que hoy también son magic numbers en code (ver A4).

---

### A4. Otros "magic numbers" que deben moverse a `META_AGGREGATION_RULES` o `META_METRIC_THRESHOLDS` — **P1**

Una vez se cree A3, estos también se promueven a configuración:

| Constante actual | Archivo | Valor | Por qué moverlo |
|-|-|-|-|
| `PSI_WINDOW_WEEKS` | `metrics/psi.py:35` | 4 | Cada modelo puede tener cadencia distinta (semanal/mensual). |
| `DECILE_WINDOW_WEEKS` | `metrics/decile_metrics.py:25` | 4 | Idem. |
| `DECILE_MIN_OBS` | `metrics/decile_metrics.py:24` | 100 | El umbral mínimo depende del volumen del modelo (un modelo de cartera comercial chica vs scoring de masa). El `decisions.md §8.2.26` dice explícitamente "mover a tabla cuando un segmento legítimamente lo necesite" — ese momento llegó cuando entre el segundo modelo. |
| `N_DECILES` | `metrics/decile_metrics.py:23` | 10 | A veces interesa quintiles para modelos chicos. |
| `EPS` | `metrics/psi.py:34` | 1e-8 | Numérico, no mover. |
| `NUM_BINS_NUMERIC` | `data/bootstrap.py:52` | 10 | Cada variable podría tener bins distintos (ya está en `binning_rules`); aquí solo es el default — OK dejarlo. |
| `MISSING_SENTINEL` | `data/bootstrap.py:51` y `data/incremental_etl.py:35` | -100 | Esto **sí** es propio del extract upstream. Promoverlo a `META_VARIABLES` por variable (con default -100). Modelos de otra fuente pueden usar otro sentinel. |

**Propuesta:** revisión cualitativa una vez A3 esté hecho — no todo va a tabla, pero `MISSING_SENTINEL` y `DECILE_MIN_OBS` sí.

---

### A5. `range(1, 12)` (segmentos hardcodeados) en bootstrap — **P0**

**Dónde está el hardcode:**
- `src/mlmonitor/data/bootstrap.py:159, 189, 325, 451`
- `src/mlmonitor/data/bootstrap_v2.py:149, 245`

Los loops asumen exactamente 11 segmentos `s1..s11` numerados con enteros consecutivos. Cualquier modelo nuevo que no tenga esa estructura (un modelo monolítico sin segmentos, o uno con 3, o uno con segmentos no numéricos) rompe el bootstrap.

**Propuesta:**
1. La fuente de verdad de los segmentos debe ser `variable_mapping.CANONICAL_VARIABLES` (que ya está keyada por seg_id). Cambiar `range(1, 12)` por `sorted(CANONICAL_VARIABLES.keys())`.
2. Idealmente, `CANONICAL_VARIABLES` y `SEGMENT_GROUP_NAMES` también dejan de vivir en código y pasan a una tabla de seed (CSV o JSON en `data/inputs/`) específica por modelo. Cuando entre el segundo modelo, el bootstrap recibe `--variables-config <path>` y lee desde ahí.

**Impacto:** alto. El bootstrap actual está atado al schema de BAZBOOST.

---

### A6. `variable_mapping.py` está atado a BAZBOOST y no escala a otros modelos — **P0**

`src/mlmonitor/data/variable_mapping.py` contiene **toda la configuración del modelo BAZBOOST** dentro del módulo:
- Mapping SERC→canónico
- `CANONICAL_VARIABLES` (variables por segmento)
- `EXTRA_SERC_VARIABLES` (variables intermedias del extract upstream)
- `SEGMENT_GROUP_NAMES` (nombres de grupos)
- `SEGMENT_FEATURE_COUNTS`

Es básicamente un **modelo declarado en código**. Para incorporar otro modelo (con otras variables, otros nombres de extract, otros grupos), habría que crear `variable_mapping_v2.py`, `_v3.py`, etc. — copy-paste sin abstracción.

**Propuesta:**
1. Crear directorio `data/inputs/model_configs/<MODEL_ID>/` con:
   - `canonical_variables.json` o `.csv` (segmento → lista de variables)
   - `name_mapping.json` (mapping del extract upstream a nombres canónicos)
   - `extra_columns.json` (columnas a ignorar)
   - `segment_metadata.json` (group_name, feature_count, descripción)
2. `bootstrap.py` recibe `model_id` como argumento, lee la configuración de ese directorio y construye los registros META.
3. `serc_to_canonical(serc_name, model_id)` ya no es una función pura del módulo, pasa a ser un método de un objeto `ModelConfig` cacheado.

**Beneficio:** un modelo nuevo se agrega creando un directorio + un schema de CSVs documentado. Cero cambios al código de `mlmonitor.data.*` para agregar el segundo modelo.

**Trade-off:** introduce una capa de "config externa" — vale la complejidad sólo si efectivamente entra un segundo modelo. Si la decisión es "monitoreamos sólo BAZBOOST_V1 y eventualmente BAZBOOST_V2", quizá baste con renombrar `variable_mapping.py` → `bazboost_v1_config.py` y asumir que cada modelo tiene su módulo. Decidir antes de implementar (probablemente la duda **D7** abierta en `dudas_documentacion.md`).

---

### A7. `categorical = vname == "fisexo"` está hardcodeado — **P1**

**Dónde:**
- `src/mlmonitor/data/bootstrap.py:196, 347`
- `src/mlmonitor/data/bootstrap_v2.py:175`

El único criterio para decidir si una variable es categórica es si su nombre es `"fisexo"`. Cualquier otra variable categórica que se introduzca en el modelo (o cualquier modelo nuevo con más categóricas) requiere modificar código.

**Propuesta:** ya existe la columna `MetaVariables.variable_type` en el schema (`numeric | categorical`). El bootstrap ya escribe ahí, pero el código que decide tipo lo hace por nombre. La fuente de verdad debe ser un dataset de configuración del modelo (ver A6) que liste qué variables son categóricas. Una vez en DB, el ETL lee `variable_type` de `META_VARIABLES` (ya lo hace en `incremental_etl.py:88-95`, ¡el mismo bootstrap es lo que está mal!).

**Fix simplificado en isolación (sin esperar A6):** mover la lista de categóricas a una constante `CATEGORICAL_VARIABLES = {"fisexo"}` en `variable_mapping.py`. Es un fix de 5 minutos que prepara el terreno.

---

### A8. Múltiples modelos en el reporte — **P2**

Hoy `report/builder.py` y `templates/fleet_report.html` asumen un solo `model_id`. Si la flota multi-modelo se reporta junta:
- Un solo PDF con secciones por modelo, o
- Un PDF por modelo enviado en el mismo email.

**Propuesta:** decisión de producto. Mi recomendación: **un PDF por modelo + un correo agregador** (email único con N adjuntos y un resumen ejecutivo de toda la flota multi-modelo en el body). Es lo más simple y respeta la división mental por equipo dueño del modelo.

Implementación:
- `PipelineOrchestrator.run_for_all_models()` itera y produce N PDFs.
- `SESEmailSender.send_multi_report(pdfs)` envía un solo correo con todos.

---

## B. Schema y modelo relacional (cambios estructurales)

### B1. Índice compuesto en `FACT_PERFORMANCE_INDIVIDUAL` — **P1**
*(ya en `docs/backlog.md §1`)*

`(model_registry_id, origination_week, ventana)` declarado en `db/models.py` y aplicado a RDS.

### B2. Índices adicionales que faltan — **P2**

Todos estos joins se ejecutan en cada pipeline run y van a degradar a medida que crezca el histórico:

| Tabla | Columnas | Justificación |
|-|-|-|
| `FACT_DISTRIBUTIONS` | `(model_registry_id, variable_id, origination_week)` | Query principal del PSI: `psi.py:78-90`. La unique constraint cubre 4 columnas pero un index compuesto en 3 acelera el scan agregado por `bin_label`. |
| `FACT_METRICS_HISTORY` | `(model_registry_id, calculation_week)` | Query principal del builder: `builder.py:336-343`. |
| `FACT_PERFORMANCE_BINNED` | `(model_registry_id, origination_week, metric_type)` | `business_metrics.py:67-76` y `business_metrics.py:205-214`. |
| `META_METRIC_THRESHOLDS` | `(valid_to)` parcial WHERE valid_to IS NULL | El `AlertEvaluator` carga **todos** los thresholds activos en cada run; un index parcial acelera ese scan en miles de millones de filas eventualmente. |

**Propuesta:** declarar todos en `db/models.py` con `Index(...)` y aplicar en RDS con un script único. Validar `EXPLAIN ANALYZE` en RDS productivo antes y después.

### B3. `FACT_METRICS_HISTORY` BI-friendly (denormalización) — **P1**
*(ya en `docs/backlog.md §3`)*

Agregar columnas `metric_name`, `target_name`, `segment_id`, `origination_week` denormalizadas. Hoy se requieren 3 JOINs y parsing de JSON `details` para tener una fila legible. Cuando el equipo conecte Power BI / Tableau directamente, esto bloquea consumo.

### B4. Desacoplar `metric_id` de SCD2 de thresholds — **P1**
*(ya en `docs/backlog.md §4`)*

El `metric_id` apunta a `META_METRIC_THRESHOLDS.id` que es versionado por SCD2; un cambio de threshold rompe el `GROUP BY metric_name` en BI. Recomiendo opción **B** (denormalizar `metric_name` en `FACT_METRICS_HISTORY` como parte del unique constraint) — más simple y compatible con B3.

### B5. Tabla nueva `META_MODELS` separada de `META_MODEL_REGISTRY` — **P3**

Hoy `META_MODEL_REGISTRY` mezcla:
- Identidad del modelo (`model_id`, `model_name`, `model_type`, `target_definition`)
- Metadata operativa por **submodelo/segmento** (`submodel_id`, `feature_count`, `model_description`, `score_min/max`)
- Auditoría (`valid_from/valid_to`, `created_at`, `owner_team`, `training_cutoff_date`)

Cuando entre un modelo monolítico (sin segmentos), todo lo de "submodelo" será NULL o duplicado N veces si se repite la identidad por cada segmento (que es lo que se hace hoy: 11 filas idénticas en columnas de identidad).

**Propuesta a futuro:**
- `META_MODELS`: identidad del modelo (1 fila por modelo).
- `META_SUBMODELS`: segmentos/submodelos (FK a `META_MODELS`, una fila por segmento; modelos sin segmentos tienen 1 fila con `submodel_id = "default"`).

**Trade-off:** es refactor de schema con migración no trivial. **Hacer sólo cuando entre un segundo modelo no segmentado**, no antes (YAGNI). Por ahora, dejarlo documentado.

### B6. `score_max` con FK en lugar de redundancia — **P3**

`score_max` aparece en:
- `MetaModelRegistry.score_max` (Integer)
- `FactDecilesHistory.score_max` (Float, este es el max observado en el decil — semántica distinta, OK)

El primero está duplicado por las 11 filas del registry (todas con valor 1000 en BAZBOOST_V1). No es crítico, pero si B5 se hace, esto se limpia automáticamente (queda en `META_MODELS`, no en `META_SUBMODELS`).

### B7. Histórico del baseline en `META_BASELINE_DISTRIBUTIONS` — **P2**
*(relacionado con `dudas_documentacion.md` D6, abierta)*

Hoy `META_BASELINE_DISTRIBUTIONS` no tiene columnas `valid_from/valid_to`. Cuando se refresque el baseline (V2 ya lo cambió por una decisión consciente, ver §8.2.29), el bootstrap **borra** y reescribe — pierdes la trazabilidad de cuándo cambió la referencia y qué PSI se computó con qué baseline. Si el equipo de crédito pregunta "¿por qué el PSI de hace 6 meses era distinto?", no hay forma de reconstruirlo.

**Propuesta:**
1. Agregar `baseline_version` (string, ej: `"2026-Q1"`) o `valid_from/valid_to` a `META_BASELINE_DISTRIBUTIONS`.
2. En `psi.py`, persistir en `FACT_METRICS_HISTORY.details` cuál `baseline_version` se usó para cada cálculo.
3. La auditoría histórica se vuelve consultable: "todos los PSI calculados con baseline 2026-Q1".

**Acoplado con D6** (abierta): hace falta decidir la cadencia de refresh antes de implementar.

---

## C. Hardcodings en código (limpieza directa)

### C1. Centralizar nombres de columnas del extract upstream — **P1**

Los nombres físicos del extract aparecen literalmente en muchos archivos:
- `fiidsegmento`, `fiidscoreds`, `fnpuntaje`, `fcvalor_variable`, `fcnombre_variable`, `fdregistro_solicitud` — bootstrap, bootstrap_v2, incremental_etl
- `semana_observacion`, `semana_num`, `flg_surtida`, `vintage`, `b_malo*` — incremental_etl, builder, charts

**Problema:** un cambio en el extract upstream (renombrar `fiidsegmento` → `id_segmento`) requiere cambiar ~10 archivos. Y otro modelo de otra área tendrá nombres distintos.

**Propuesta:** módulo `data/raw_schema.py` con una clase / dataclass por extract:
```python
class VariablesSercSchema:
    credito_id = "fiidscoreds"
    segment_id = "fiidsegmento"
    score = "fnpuntaje"
    variable_name = "fcnombre_variable"
    variable_value = "fcvalor_variable"
    request_timestamp_ms = "fdregistro_solicitud"

class MuestraWeeklySchema:
    credito_id = "fiidscoreds"
    segment_id = "fiidsegmento"
    score = "fnpuntaje"
    disbursement_iso_week = "semana_num"
    observation_iso_week = "semana_observacion"
    is_disbursed = "flg_surtida"
    is_baz_boost = "flg_baz_boost"
    targets = ("b_malo2_4", "b_malo4_6", "b_malo8_13", "b_malo8_16", "b_malo14_26", "b_malo14_52")
```
El ETL pasa de `df["fiidsegmento"]` a `df[VariablesSercSchema.segment_id]`. Costo de la refactorización: ~1h. Beneficio: un solo punto de cambio si cambia el extract; cero si cambia un nombre internamente; multi-modelo lo extiende a `BazBoostSchema` vs `OtroModeloSchema`.

### C2. `MetricsCalculator` doble-trabajo en idempotencia — **P2**

`metrics/calculator.py:148-166`: el calculator construye **todos** los `FactMetricsHistory` en memoria, y luego para cada uno hace un query de existencia. Si la semana ya tiene métricas (re-run idempotente), igual computa todos los valores y solo evita los inserts.

**Problema:** el cómputo (PSI, Gini, KS) se hace dos veces, no solo el insert. Sumado a que el unique constraint de la tabla ya garantiza la idempotencia a nivel DB, hace doble trabajo.

**Propuesta:**
1. Antes de calcular las métricas del segmento, query a `FACT_METRICS_HISTORY` para ver si ya hay filas para `(model_registry_id, calculation_week)`. Si sí, **skip** del cómputo entero del segmento.
2. Si quieres mantener la opción de recalcular (correcciones), agregar flag `--force-recompute` que primero hace `DELETE` por `(model, week)` y luego inserta.

**Beneficio operativo:** un re-run inocente (volver a correr el pipeline en la misma semana) deja de tomar 3 min y pasa a 5s.

### C3. `print()` por todos lados — **P2**

Counté ~30 `print(...)` en código de runtime (`pipeline/orchestrator.py`, `report/renderer.py`, `email/sender.py`, `storage/s3_uploader.py`, etc.). En CloudWatch Logs aparecen como stdout sin nivel — un error no se distingue de un info.

**Propuesta:**
1. Migrar todos los `print` a `logger.info/warning/error` con el `logging` estándar.
2. Configurar formato JSON-lines en `entrypoint.sh` para CloudWatch (filtrable y consultable):
   ```python
   logging.basicConfig(format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
   ```
3. El logger ya existe en `bootstrap.py`, `incremental_etl.py`, `bootstrap_v2.py` — sólo replicar el patrón en los demás módulos.

**Beneficio operativo:** errores críticos detectables con un filtro en CloudWatch Insights.

### C4. Typo: `tresholds_monitoreo.csv` y `warning_treshold` / `critical_treshold` — **P3**

El CSV viene con typo del equipo de crédito (`treshold` en lugar de `threshold`). El código respeta el typo en `threshold_loader.py:106-107`. No es bloqueante pero contagia el typo a archivos del repo.

**Propuesta menor:** mantener compatibilidad leyendo ambos nombres en `parse_thresholds_csv` (try `threshold` primero, fallback a `treshold`); cuando crédito regenere el CSV se podrá retirar la rama de fallback. Documentar que el formato preferido es `threshold`.

### C5. `LOGO_PATH` hardcoded en `renderer.py` — **P3**

`src/mlmonitor/report/renderer.py:15`:
```python
LOGO_PATH = Path("artifacts/images/elektra-logo.png")
```

Cuando entre un segundo modelo de otra área (digamos, riesgo operacional), el logo a usar es distinto. Multi-modelo y multi-area conviene moverlo a `MetaModelRegistry.logo_path` o a un directorio convencional `artifacts/images/<model_id>.png`.

### C6. Templates HTML duplican strings de marca y supuestos del modelo — **P3**

Revisando `report/templates/fleet_report.html`:
- "Reporte de Monitoreo de Segmentos" (asume modelo segmentado)
- "Sub-scorecards monitoreados" (asume scorecard logístico)
- Términos del prompt LLM ("scorecards de crédito", "BAZBOOST", "México") en `analyst/prompts.py`

Para multi-modelo, necesitas:
- Templates parametrizables por `model.model_type` (scorecard vs xgboost vs etc.)
- Prompt LLM por área (riesgo crediticio vs fraude vs operacional)

**Propuesta:** convertir el prompt en una **plantilla por modelo** con clave `MetaModelRegistry.prompt_template_name`. Los templates HTML se pueden mantener si se les añaden bloques Jinja `{% block %}` y `{% extends %}` para sobrescribir.

**Trade-off:** complejidad medible. Hacer **sólo cuando entre el segundo modelo**.

---

## D. Calidad de código y robustez

### D1. Tests faltantes para flujos end-to-end — **P1**
*(ya en `docs/backlog.md §2`)*

Tests para `ModelBootstrap.run()` y `IncrementalETL.run()` end-to-end con SQLite `:memory:` y CSVs sintéticos. Esto bloquearía cambios silenciosos al parser de CSVs o al binning.

**Adicional propuesto:** test end-to-end del pipeline completo con SQLite + LLM mockeado + S3/SES mockeados (con `moto`). Hoy todo se prueba en aislamiento; el smoke test depende de correr `run_pipeline.py` manualmente. Un test smoke en CI cierra esa brecha.

### D2. CI con dos perfiles de instalación — **P1**
*(ya en `docs/backlog.md §5`)*

GitHub Actions con dos jobs: `poetry install --only main` (verifica que ETL no requiere pipeline) y `poetry install --with pipeline,dev`.

### D3. `try/except: pass` en `bedrock_analyst.py:124` — **P2**

```python
except json.JSONDecodeError:
    pass
```

Si el LLM devuelve JSON malformado (porque cambió el modelo o el prompt), el parsing falla silenciosamente y la lista de acciones queda vacía sin alarma. Mínimo loggear el error con `logger.warning` y enviar el raw response a `details` para diagnóstico.

### D4. Reintentos para llamadas a Bedrock/SES/S3 — **P2**

Bedrock, SES y S3 pueden fallar por throttling o errores transitorios. Hoy una sola llamada → un solo intento → pipeline falla.

**Propuesta:** wrapper `tenacity` con backoff exponencial (3 reintentos, 2s base) para los `boto3.client(...).invoke_model/send_raw_email/upload_file`. Bedrock especialmente sensible — los segmentos se llaman 1 a 1 (12 llamadas por run); 1 throttle rompe el run completo.

### D5. Sin context manager para sessions de LLM — **P3**

`bedrock_analyst.py` itera sobre 11 segmentos llamando a Bedrock secuencialmente. Si entre el segmento 5 y el 6 falla la red, los 5 primeros ya están perdidos (no hay caché).

**Propuesta:** caché en memoria de `raw_responses` para reanudación parcial. Más ambicioso: persistirlas en una tabla `FACT_LLM_RESPONSES` (granular, debuggeable, replayable). Bajo prioridad — el LLM no es crítico para el reporte.

### D6. Manejo de errores en `PipelineOrchestrator.run` — **P2**

Hoy si el step 2 (`builder.build`) falla, el step 1 (cálculo de métricas) **ya está commiteado** porque el `with get_session(...)` se cerró exitosamente. Si el step 4 (email) falla, igualmente; pero si el step 1 falla todo se rollbackea. Inconsistencia.

**Propuesta:** dejar explícito el contrato de transaccionalidad. Cada step debe cargar/escribir en su propia sesión y declarar si es atómica respecto al run o respecto al step. La regla actual genera buena UX (los datos se persisten aunque falle el LLM/email), pero no está documentada.

Documentar en docstring de `PipelineOrchestrator.run`:
> Step 1 (métricas) se commitea independientemente: si falla cualquier step posterior (LLM, PDF, S3, email) los datos calculados quedan persistidos. Esto permite re-runs solo del PDF/email sin recálculo.

### D7. `bootstrap.py` y `bootstrap_v2.py` coexistiendo — **P1**

§8.2.29 explícitamente lo deja como deuda: "consolidar `bootstrap.py` y `bootstrap_v2.py` cuando V2 esté validado en RDS". V2 ya está validado. Hay riesgo de:
- Cambios al bootstrap V1 que se olviden de aplicar a V2
- Confusión sobre cuál es "el oficial" para nuevos colaboradores
- Duplicación: ~80% del código es idéntico entre ambos

**Propuesta concreta:**
1. Renombrar `bootstrap.py` → `bootstrap_legacy.py` y mantenerlo solo como referencia histórica para el path WIDE (`base_train_test_bb.csv`).
2. Renombrar `bootstrap_v2.py` → `bootstrap.py` y promover `ModelBootstrapV2` → `ModelBootstrap`.
3. Si se quiere preservar el path WIDE como fallback, agregar un flag `--source serc | base_train` y dispatchar el método de baseline correspondiente.
4. Actualizar `run_bootstrap.py` y eliminar `run_bootstrap_v2.py`.

**Acoplado con A6:** una vez se centralice la config por modelo, el "qué fuente usar para el baseline" puede vivir en la config del modelo, no en flag CLI.

### D8. Validaciones defensivas faltantes en `_aggregate_status` — **P2**

`builder.py:82` filtra `psi_max` para no doble-contar pero el comentario dice "ya está cubierto por psi_<variable> individuales y por psi_score". Esa garantía depende de la consistencia entre el cálculo y el consumo. Si en algún momento `psi_max` aparece sin un `psi_<variable>` correspondiente, el conteo se desincroniza silenciosamente. Sumar test `tests/test_status_aggregation.py::test_psi_max_excluded_from_count` que verifique explícitamente la regla.

### D9. `score_max` no nulo en `_calculate_segment_metrics` — **P3**

`metrics/calculator.py:143`: `score_max=seg.score_max or 1000`. Si por error un registro de `META_MODEL_REGISTRY` queda con `score_max=NULL`, se usa silenciosamente 1000. Mejor `if seg.score_max is None: raise ValueError(...)` — es mejor fallar fuerte que producir Gini/KS incorrectos.

---

## E. Operaciones, observabilidad y deploy

### E1. Sin métricas operativas del pipeline — **P1**

No se persiste:
- Cuánto tardó cada step
- Si el LLM falló o respondió en tiempo
- Cuántos segmentos quedaron sin Gini/KS por falta de cohorte
- Cuántas veces se reintentó cada llamada Bedrock/S3

**Propuesta:** tabla `FACT_PIPELINE_RUNS`:
```python
class FactPipelineRuns(Base):
    __tablename__ = "FACT_PIPELINE_RUNS"
    id = Column(Integer, primary_key=True)
    model_id = Column(String, nullable=False)
    calculation_week = Column(Date, nullable=False)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime)
    status = Column(String)  # "success" | "failed" | "partial"
    metrics_step_seconds = Column(Float)
    report_step_seconds = Column(Float)
    llm_step_seconds = Column(Float)
    pdf_step_seconds = Column(Float)
    email_step_seconds = Column(Float)
    s3_uri = Column(String)
    error_message = Column(Text)
    fleet_summary = Column(JSONText)  # {ok, warning, critical}
```

**Beneficio:** al cabo de 3-6 meses tienes una serie temporal de "tiempo del pipeline" — útil para detectar degradación de Bedrock o crecimiento del dataset.

### E2. Sin alarmas de falla — **P1**

Hoy si el cron de EventBridge falla (Bedrock con throttling, RDS caída, etc.) no llega notificación. El usuario solo se entera cuando *no llega el correo*.

**Propuesta:** SNS topic `mlmonitor-failures` con email subscription al usuario. Configurar:
- CloudWatch Alarm: ECS task con exit ≠ 0 → SNS
- CloudWatch Alarm: ningún PDF subido a S3 en las últimas 24h post-schedule → SNS
- En `PipelineOrchestrator.run`, on-exception, publicar en SNS con error y stack.

### E3. Container heath/readiness — **P3**

Cuando se mueva el ETL a streaming/batch upstream automatizado, conviene un endpoint de health para auditoría externa. Por ahora el container es one-shot, no aplica.

### E4. CI/CD para build + push a ECR — **P1**
*(ya identificado en `decisions.md §8.2.20`)*

GitHub Actions con:
- Job de tests + lint
- Job de build de imagen Docker + push a ECR
- Job de update de task definition (con tag `:latest` y `:vN.N.N`)

### E5. Terraform para infra — **P2**
*(ya identificado en `decisions.md §8.2.20`)*

Importar la infra existente con `terraform import`. Una vez en código, los cambios futuros son auditables y revertibles.

### E6. Cerrar SG de RDS — **P0**
*(ya identificado en `decisions.md §8.2.20`)*

`sg-02e9d008b587402f7` permite `0.0.0.0/0:5432`. Cerrar a `sg-0c54b54ed399b471c` (Fargate). Es deuda de seguridad explícita.

### E7. Salir de SES sandbox — **P1**
*(ya identificado en `decisions.md §8.2.20`)*

Mientras esté en sandbox, hay un solo destinatario verificado. Cualquier nuevo recipient requiere aprobación de AWS. Bloqueante para el rollout multi-area.

### E8. Lifecycle policies en S3 — **P2**

`dudas_documentacion.md` D3 confirma que el bucket no tiene lifecycle. Acumular PDFs semanales por años sin política es deuda.

**Propuesta:**
- Transición a Glacier después de 90 días.
- Expiración después de 7 años (alineado con regulación crediticia mexicana, validar con compliance).

---

## F. Tests y validación

### F1. Tests para multi-modelo — **P1**
*(depende de A1, A2)*

Una vez se haga A1+A2 (eliminar `BAZBOOST_V1` del runtime), agregar test:
- Bootstrap con 2 modelos en la misma DB
- Pipeline procesa ambos sin colisión de `model_registry_id`
- Reporte separa los modelos correctamente

### F2. Property-based tests para `compute_psi_from_df` y `compute_gini_ks_individual` — **P3**

Funciones puras matemáticas son candidatas perfectas para `hypothesis`:
- "PSI de una distribución contra sí misma = 0"
- "Gini permanece igual ante reescalado lineal del score"
- "KS está en [0, 1]"

Cierran clases de bugs sutiles (overflow, edge cases con bins vacíos).

### F3. Validación contra notebook congelado en CI — **P2**

`notebooks/validacion_metricas_baseline.ipynb` es ground truth manual. Hoy no se ejecuta en CI. Cualquier cambio al cálculo que pase los unit tests pero rompa el notebook se detecta tarde.

**Propuesta:** convertir el notebook en un script `scripts/validate_against_baseline.py` que se ejecute en CI con un dataset de fixtures pequeño. Si los números divergen >1e-4, falla.

### F4. Cobertura de tests — **P3**

Hoy no hay reporte de cobertura. Agregar `pytest-cov` (ya está en deps) al CI con failure threshold (80%?) — al menos visualizar deuda de cobertura.

---

## G. Seguridad y operación

### G1. Secretos manager: dos secretos para una sola configuración — **P3**

`config/secrets_loader.py` carga `ml-monitoring/rds` y `ml-monitoring/SES`. Funciona pero es N+1 calls (una por secreto). Multi-modelo / multi-area podría requerir 5-10 secretos.

**Propuesta:** consolidar en un único secreto `ml-monitoring/config` con todas las claves agrupadas por sección. Reduce llamadas a Secrets Manager (cuesta dinero a escala) y centraliza la configuración.

### G2. PII en logs / S3 — **P2**

Auditar:
- Los logs no deben emitir scores de créditos individuales (ya parecen no hacerlo).
- Los PDFs no deben incluir IDs de crédito (verificar — hoy parece OK pero conviene añadir test que falle si encuentra patrones de ID).

### G3. Encriptación at-rest — **P2**

- RDS: confirmar `StorageEncrypted=true`.
- S3 bucket: verificar SSE-S3 o SSE-KMS habilitado.
- Los PDFs incluyen métricas agregadas (no PII directo) pero igual tienen valor competitivo.

---

## H. Documentación y mantenimiento

### H1. README.md raíz no existe — **P2**

El proyecto no tiene `README.md` — un colaborador nuevo arranca con `CLAUDE.md` (que es para el agente). Conviene un README con quickstart humano.

### H2. `docs/architecture/architecture.md` está incompleto — **P3**

Existe pero no fue revisado en esta auditoría — vale la pena pasada general una vez se hagan A1-A2.

### H3. Diccionarios de variables como CSVs versionados — **P3**

`Dicionario_Variables_BB.csv` y `Dicionario_Segmentos_BB.csv` están en `data/inputs/raw_tables/` (no commiteados, en `.gitignore`?). Si se pierden no se reconstruye fácilmente. Verificar que estén respaldados (S3 o git LFS).

### H4. ADRs deben citar el archivo + commit — **P3**

Los `decisions.md` actuales mencionan archivos pero no commits. Para auditoría histórica, "este ADR aplicó en commit `abc1234`" facilita ir atrás. Convención que se puede adoptar a partir de ahora.

---

## I. Refactors menores ya identificados

| Item | Archivo | Notas |
|-|-|-|
| Eliminar imports no usados | varios | `ruff` o `flake8` lo detectaría |
| Anotaciones de tipo incompletas (especialmente returns de queries SQLAlchemy) | módulos `*.py` | mypy en strict mode haría visible la deuda |
| Funciones de >100 líneas | `incremental_etl._flow_b_performance`, `bootstrap._populate_meta_variables`, `report.builder._build_segment_metrics` | descomponer en funciones más chicas con responsabilidad única |
| Duplicación entre `bootstrap.py` y `bootstrap_v2.py` (`_bin_numeric_baseline` vs `_bin_numeric_baseline_v2`) | `data/` | resolver con D7 |
| Comentarios en español + inglés mezclados | varios | adoptar política (recomiendo español, alineado con el dominio del usuario) |

---

## J. Resumen — orden recomendado de ataque

Si tuviera que priorizar las próximas iteraciones:

**Iteración 1 (preparar multi-modelo, P0):**
- A1 — quitar `BAZBOOST_V1` hardcoded
- A2 — `primary_target` a `META_MODEL_REGISTRY`
- A5 — `range(1, 12)` derivado de config
- E6 — cerrar SG de RDS

**Iteración 2 (limpieza estructural, P1):**
- A3 — reglas de agregación a tabla
- A4 — `MISSING_SENTINEL` a `META_VARIABLES`, `DECILE_MIN_OBS` a config
- D7 — consolidar bootstrap y bootstrap_v2
- B1 — índice compuesto en `FACT_PERFORMANCE_INDIVIDUAL`
- C1 — schema literals del extract en módulo central
- C3 — migrar prints a logger

**Iteración 3 (BI / observabilidad, P1):**
- B3 — denormalización en `FACT_METRICS_HISTORY`
- B4 — desacoplar `metric_id` de SCD2
- E1 — `FACT_PIPELINE_RUNS` para observabilidad
- E2 — alarmas SNS

**Iteración 4 (multi-modelo profundo, P0/P2 según D7 abierta):**
- A6 — `data/inputs/model_configs/<MODEL_ID>/` con configuración externalizada
- A7 — categóricas declaradas, no inferidas
- A8 — multi-modelo en reportes/email
- F1 — tests multi-modelo
- C5/C6 — logo y templates por modelo

**Iteración N (CI/CD, calidad):**
- D1, D2, F3 — tests end-to-end + CI
- E4, E5 — CI/CD a ECR + Terraform

---

## K. Lo que **no** propongo cambiar

Vale la pena explicitar qué **funciona bien** y conviene preservar:

- **SCD2 en META + append-only en FACT.** Patrón sólido, ya validado por el notebook.
- **Auto-detección de fechas (`semana_observacion` → execution_week).** §8.2.10. Previene bugs de fecha.
- **Separación bootstrap / ETL / pipeline.** §8.2.1. Facilita backfill y debugging.
- **Idempotencia por `UniqueConstraint` + EXISTS check.** §8.2.7. Robusto.
- **Lazy imports de boto3.** Permite que ETL corra sin AWS.
- **Plantillas Jinja para HTML y prompts LLM.** Bien separadas, fácil de cambiar.
- **`compute_psi_from_df` y `compute_gini_ks_individual` como funciones puras.** Testeables, reusables.
- **Ventana rodante de 4 semanas para PSI / null_rate / deciles.** §8.2.27, §8.2.28. Decisión correcta.
- **Bedrock con `claude-haiku-4-5` para narrativas.** Razonable; el coste no justifica Sonnet.

El proyecto está sano. Las mejoras son evolución incremental, no rewrites.

---

## L. Checklist de avance (estado actual)

Trazabilidad de qué items de este documento ya quedaron cerrados, parcialmente cubiertos, o todavía pendientes. Para el detalle de cada cierre, ver `devlog.md` (bitácora por fecha) y `docs/decisions.md` (ADRs formales).

### Iteración 1 — Multi-modelo base (cerrada 2026-05-07)

Ámbito: que la herramienta deje de asumir que sólo existe `BAZBOOST_V1`. Verificada localmente con 131/131 tests pasando, re-bootstrap + ETL (ventana rodante de 4 semanas) + pipeline end-to-end.

- [x] **A1** — Eliminar `MODEL_ID = "BAZBOOST_V1"` hardcodeado en pipeline/orchestrator, ETL, bootstrap, scripts. Auto-detección plural desde `META_MODEL_REGISTRY` cuando no se pasa `--model-id`. Helper `mlmonitor.data.model_registry.resolve_model_ids`. ADR §8.2.30.
- [x] **A2** — `primary_target` movido a nueva columna `MetaModelRegistry.primary_target_variable` (String nullable). Bug latente del default desincronizado en `AnalysisContext` corregido. ADR §8.2.30.
- [x] **A5** — Eliminar `range(1, 12)` hardcodeado. Los segmentos se iteran desde `config.segments`. Unificado bajo A6 (no hay flag `--variables-config <path>` separado).
- [x] **A6** — Configuración por modelo externalizada a `data/inputs/model_configs/<model_id>/config.json` + 3 CSVs de catálogo. Nuevo módulo `mlmonitor.data.model_config` con `ModelConfig` dataclass. Módulo `variable_mapping.py` **eliminado**. ADR §8.2.30.
- [x] **A7** — Categóricas declaradas, no inferidas. `is_categorical` lee de `config.categorical_variables` en lugar del literal `vname == "fisexo"`. (Cerrado como side-effect de A6.)
- [x] **Fix tests pre-existentes** — Los 6 fallos de `tests/test_status_aggregation.py` (deuda del cambio de umbrales del 2026-05-05) reescritos para parametrizar con `settings.status_*_count_*`. Robustos a cambios futuros.

**Pendiente operativo (fuera de scope de código local):**
- [ ] Drop+rebuild en RDS productivo + re-bootstrap + backfill (autorización del usuario; difertido hasta cerrar más desarrollo local).
- [ ] Smoke test en ECS Fargate con la imagen actualizada.

### Iteración 2 — Limpieza estructural (cerrada 2026-05-11)

Ámbito: sacar configuración de severidad y ventanas de cómputo del código, consolidar el bootstrap V1+V2. Verificada localmente con suite 158/158 + drop+rebuild + ETL ventana rodante (4 semanas 2026-03-16..2026-04-06) + pipeline end-to-end.

- [x] **A3** — Reglas de agregación de severidad (`status_*_count_*`) movidas de `config/settings.py` a nueva tabla SCD2 `META_AGGREGATION_RULES`. Resolver `data/aggregation_rules.py::load_aggregation_rules` con precedencia specific → global → defaults Python (mismo patrón que `AlertEvaluator.get_threshold`). Seed idempotente en bootstrap (`seed_default_global_rules`, valid_from=2025-01-01). `_aggregate_status` y `_build_severity_legend` aceptan el dict resuelto.
- [x] **A4** — `psi_window_weeks`, `decile_window_weeks`, `decile_min_obs`, `n_deciles`, `baseline_year`, `baseline_n_weeks` movidos a `ModelConfig` con defaults vía `data.get(..., default)` (backwards-compat, sin tocar `required`). `MetricsCalculator(session, config=...)` los lee y los pasa a `get_psi_for_all_variables`, `get_null_rates`, `get_decile_data_for_segment` (firma ampliada con `n_deciles` y `window_weeks`). Constantes de módulo se mantienen como defaults de kwarg.
- [x] **MISSING_SENTINEL** — Descartado per-variable: se queda model-wide en `ModelConfig.missing_sentinel` (config.json). Razones documentadas en `devlog.md` (2026-05-11): es marcador, no valor; uniforme del extract upstream; sin colisiones en BAZBOOST_V1. YAGNI hasta que un modelo legítimamente lo necesite.
- [x] **D7** — `bootstrap.py` y `bootstrap_v2.py` consolidados en un solo `ModelBootstrap` con el baseline V2 (variables_serc, oficial per ADR §8.2.29). Path WIDE (`base_train_test_bb.csv`) retirado del código (recuperable desde git si fuera necesario). `scripts/run_bootstrap_v2.py` eliminado; `scripts/run_bootstrap.py` absorbió los flags `--year`, `--n-weeks`, `--variables-serc-file`.

**Tests:** suite final 158 passed (+11 vs cierre Iter 1): +8 nuevos en `test_aggregation_rules.py`, +3 en `test_model_config.py` para los campos A4. `test_status_aggregation.py` adaptado para parametrizar desde `DEFAULT_AGGREGATION_RULES` en vez de `settings`. `conftest.py` siembra reglas globales en el fixture `populated_engine`.

**Documentación de cierre:**
- ADR formal en `docs/decisions.md §8.2.31` con scope completo, decisión MISSING_SENTINEL, schema de `META_AGGREGATION_RULES`, supersede §8.2.29 (parte de consolidación de bootstrap).
- `docs/architecture/data_model.md`: nueva §2.4 `META_AGGREGATION_RULES`, §1.7 (MISSING_SENTINEL) actualizada para reflejar que vive en `ModelConfig`, §4.7 (`overall_status`) ahora apunta a la tabla en vez de a `settings.py`.
- `status_*_count_*` eliminados de `config/settings.py` (verificado con grep: cero referencias en código activo).

**Pendiente operativo (fuera de scope de código local):**
- [ ] Drop+rebuild en RDS productivo + re-bootstrap + backfill (autorización del usuario).
- [ ] Smoke test en ECS Fargate con la imagen actualizada.

### Iteración 3 — BI / observabilidad (cerrada 2026-05-18)

Ámbito: índices que evitan degradación al crecer el histórico en RDS + histórico operativo del pipeline persistido + runbook de alarmas SNS. Verificada localmente con suite **172/172** + drop+rebuild + ETL ventana rodante (4 semanas 2026-03-16..2026-04-06) + pipeline end-to-end + 2 re-runs append-only.

- [x] **B1** — `Index("ix_fact_perf_individual_lookup", "model_registry_id", "origination_week", "ventana")` agregado en `FactPerformanceIndividual.__table_args__` (`db/models.py`). La UC actual `(credito_id, model_registry_id, ventana)` no cubría el query pattern del Gini/KS.
- [x] **B2** — Alcance reducido tras auditoría:
  - `FACT_DISTRIBUTIONS` y `FACT_METRICS_HISTORY` quedaron **descartados** del scope porque la `UniqueConstraint` actual ya los cubre como prefijo (UC = `(model_registry_id, variable_id, origination_week, bin_label)` cubre `(model_registry_id, variable_id, origination_week)`; idem para `FACT_METRICS_HISTORY`).
  - `Index("ix_fact_perf_binned_metric", "model_registry_id", "origination_week", "metric_type")` agregado — la UC tiene `execution_week` intercalado y no sirve como prefijo.
  - `Index("ix_meta_metric_thresholds_active", "valid_to", postgresql_where=text("valid_to IS NULL"), sqlite_where=text("valid_to IS NULL"))` — índice parcial, primero en el codebase; acelera el scan del `AlertEvaluator`.
- [x] **E1** — Nueva tabla SCD-no-aplica/append-only `FACT_PIPELINE_RUNS` (`db/models.py`) + módulo `pipeline/run_recorder.py::PipelineRunRecorder` con mini-sesiones independientes que sobreviven a rollbacks de los steps. `PipelineOrchestrator.run()` instrumentado con timings `time.perf_counter` por step + try/except global que persiste `error_message` + `error_stack` y re-lanza. Helper `data/model_registry.py::resolve_model_registry_id` para mapear `model_id → META_MODEL_REGISTRY.id` (primer submodel_id activo). Re-runs generan filas nuevas; BI usa `MAX(started_at)` para el más reciente.
- [x] **E2** — Documentación pura de infra (sin código). Nueva §4 en `docs/infrastructure/aws_deployment.md` con runbook completo: SNS Topic `mlmonitor-failures`, log metric filter sobre `/ecs/mlmonitor` (`MLMonitor/PipelineFailures`), CloudWatch Alarm (≥1 evento en 5 min) → SNS, snippet de smoke test (`run-task` con `exit 1`). El path complementario (auditoría histórica en RDS) ya está cubierto por la tabla `FACT_PIPELINE_RUNS` de E1.

**Tests:** suite final **172 passed** (+10 vs Iter 2): +4 en `test_models_indexes.py`, +6 en `test_pipeline_run_recorder.py`. Cero regresiones.

**Diferimiento explícito (B3 y B4)** — decisión del 2026-05-18: B3 (denormalizar `metric_name`/`target_name`/`segment_id`/`origination_week` en `FACT_METRICS_HISTORY`) y B4 (desacoplar `metric_id` de la SCD2 de thresholds) **no se implementan en esta iteración**. Razón: se prefiere mantener la normalización máxima del esquema estrella aunque eso obligue a JOINs adicionales en BI. Trade-off asumido. Se re-evaluará cuando el consumo desde BI (Power BI / Tableau) se vuelva un dolor real medible — hasta entonces los JOINs son aceptables. Movidos a la sección final de descartados.

**Documentación de cierre:**
- ADR formal en `docs/decisions.md §8.2.32` con scope completo, justificación de `model_registry_id` (no `model_id` string), justificación de append-only, decisión de B3/B4.
- `docs/architecture/data_model.md` actualizado — nueva §3.6 `FACT_PIPELINE_RUNS`.
- `docs/infrastructure/aws_deployment.md §4` con runbook E2.

**Pendiente operativo (fuera de scope de código local):**
- [ ] Drop+rebuild en RDS productivo + re-bootstrap (los índices nuevos se aplican vía `Base.metadata.create_all`; snippet `CREATE INDEX IF NOT EXISTS` para aplicación incremental sin drop está documentado abajo) (autorización del usuario).
- [ ] Crear SNS Topic + email subscription + log metric filter + CloudWatch Alarm en AWS (runbook en `docs/infrastructure/aws_deployment.md §4`).
- [ ] Smoke test en ECS Fargate con la imagen actualizada.

**Snippet de aplicación incremental de índices en RDS (sin drop):**
```sql
CREATE INDEX IF NOT EXISTS ix_fact_perf_individual_lookup
  ON "FACT_PERFORMANCE_INDIVIDUAL" (model_registry_id, origination_week, ventana);
CREATE INDEX IF NOT EXISTS ix_fact_perf_binned_metric
  ON "FACT_PERFORMANCE_BINNED" (model_registry_id, origination_week, metric_type);
CREATE INDEX IF NOT EXISTS ix_meta_metric_thresholds_active
  ON "META_METRIC_THRESHOLDS" (valid_to) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS ix_fact_pipeline_runs_lookup
  ON "FACT_PIPELINE_RUNS" (model_registry_id, calculation_week, started_at);
```

### Iteración 4 — Multi-modelo profundo (P2/P3, depende del segundo modelo)

- [ ] **A8** — Multi-modelo en reportes/email (un PDF por modelo, correo agregador). Hasta que entre el segundo modelo no es urgente.
- [ ] **F1** — Tests específicos de multi-modelo (DB con 2 model_id distintos, no colisionan).
- [ ] **C5** — `LOGO_PATH` por modelo. (Hoy hardcoded en `report/renderer.py:15`.)
- [ ] **C6** — Templates HTML y prompts LLM por área/modelo.

### Iteración N — CI/CD y calidad (P1/P2)

- [ ] **D1** — Tests end-to-end de `ModelBootstrap.run()` y `IncrementalETL.run()` con SQLite `:memory:` + CSVs sintéticos.
- [ ] **D2** — CI con dos perfiles de instalación (`--only main` vs `--with pipeline,dev`).
- [ ] **D3** — Quitar el `try/except: pass` silencioso en `bedrock_analyst.py:124`.
- [ ] **D4** — Reintentos con backoff para Bedrock/SES/S3.
- [ ] **D5** — Resumen incremental del LLM (caché para reanudación parcial).
- [ ] **D6** — Documentar contrato transaccional de cada step en `PipelineOrchestrator.run`.
- [ ] **D8** — Test explícito para regla `psi_max` excluida del conteo.
- [ ] **D9** — `score_max=None` → fail-fast en `MetricsCalculator`.
- [ ] **E3** — Health/readiness endpoint del contenedor (no urgente; one-shot job).
- [ ] **E4** — CI/CD GitHub Actions (build + push a ECR + update task definition).
- [ ] **E5** — Terraform con `terraform import` de la infra existente.
- [ ] **F2** — Property-based tests para `compute_psi_from_df` y `compute_gini_ks_individual`.
- [ ] **F3** — Validación contra notebook congelado en CI.
- [ ] **F4** — Reporte de cobertura con `pytest-cov` y threshold mínimo.

### Operación / Seguridad / Docs

- [ ] **E6** — Cerrar `sg-02e9d008b587402f7` a sólo `sg-0c54b54ed399b471c` (hoy `0.0.0.0/0:5432`). Identificado en ADR §8.2.20.
- [ ] **E7** — Salir de SES sandbox.
- [ ] **E8** — Lifecycle policies en S3 (Glacier después de 90 días, expiración por compliance).
- [ ] **C1** — `data/raw_schema.py` con clase por extract (campos `fiidsegmento`, `fnpuntaje`, etc. centralizados).
- [ ] **C2** — `MetricsCalculator` skip si la semana ya tiene métricas (`--force-recompute` flag para sobrescribir).
- [ ] **C3** — Migrar `print()` a `logger` en orchestrator, renderer, sender, s3_uploader.
- [ ] **C4** — Compatibilidad con typo `tresholds` → `thresholds` en CSV. (Parcialmente cerrado en A6: el archivo se renombró a `thresholds.csv` en `model_configs/bazboost_v1/`. El header del CSV todavía tiene `warning_treshold` / `critical_treshold` — pendiente cuando crédito regenere el CSV.)
- [ ] **G1** — Consolidar secretos AWS en uno solo (`ml-monitoring/config`).
- [ ] **G2** — Auditar PII en logs y PDFs.
- [ ] **G3** — Verificar encriptación at-rest en RDS y S3.
- [ ] **H1** — README.md raíz para humanos.
- [ ] **H2** — Revisar `docs/architecture/architecture.md` post-iteración 1.
- [ ] **H3** — Verificar respaldo de los CSVs de diccionario (ahora en `model_configs/bazboost_v1/`, ya versionados en git → cerrado).
- [ ] **H4** — ADRs citan commit hash. (Convención a adoptar para los próximos ADRs.)

### Items que quedaron descartados o consolidados

- **B3** — Denormalizar `FACT_METRICS_HISTORY` con `metric_name`, `target_name`, `segment_id`, `origination_week` para BI directo. **Diferido en Iter 3 (2026-05-18)**: se prefiere mantener la normalización máxima del esquema estrella aunque obligue a JOINs en BI. Re-evaluar cuando el consumo desde Power BI / Tableau se vuelva un dolor real medible (queries lentas, esfuerzo de modelado, mantenimiento de vistas materializadas, etc.).
- **B4** — Desacoplar `metric_id` de SCD2 de thresholds (denormalizar `metric_name` en `FACT_METRICS_HISTORY`). **Diferido en Iter 3 (2026-05-18)** por la misma razón que B3 — son cambios complementarios y se atacarán juntos cuando entre el primer caso de uso real de BI.
- **B5** — Tabla `META_MODELS` separada de `META_MODEL_REGISTRY`: P3, esperar al segundo modelo no segmentado para evaluarlo. No hacer especulativamente.
- **B6** — `score_max` con FK: queda absorbido en B5 si se hace; bajo prioridad.
- **B7** — Histórico SCD2 de `META_BASELINE_DISTRIBUTIONS`: acoplado a la duda D6 (refresco del baseline). Esperar a que el usuario defina cadencia de refresco.
- **A5** — Cerrado dentro de A6 (no es problema separado).
- **A7** — Cerrado dentro de A6 (las categóricas se declararon en `config.json`).
