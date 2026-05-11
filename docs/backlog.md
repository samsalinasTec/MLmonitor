# Backlog de ingeniería — MLMonitor

Lista viva de pendientes accionables que **no son bugs ni features**, sino deuda técnica y preparación operativa. Para ADRs ver [`decisions.md`](./decisions.md); para bitácora de sesiones ver [`../devlog.md`](../devlog.md).

Cada item indica: contexto mínimo, por qué importa, y criterio de hecho.

---

## 1. Índice compuesto en `FACT_PERFORMANCE_INDIVIDUAL`

**Contexto:** Gini/KS se calculan desde `FACT_PERFORMANCE_INDIVIDUAL` (una fila por crédito). Los queries filtran por `(model_registry_id, origination_week, ventana)`.

**Por qué:** Hoy en SQLite dev los ~28K registros por cohorte son imperceptibles, pero en Postgres productivo con 1M+ créditos el scan secuencial se nota. Agregar el índice antes de que el volumen crezca evita downtime de reindex.

**Criterio de hecho:** `CREATE INDEX ix_fact_performance_individual_lookup ON "FACT_PERFORMANCE_INDIVIDUAL" (model_registry_id, origination_week, ventana)` aplicado en RDS (y declarado en `db/models.py`), con un test que verifique el `EXPLAIN` usa el índice.

---

## 2. Tests unitarios de bootstrap y ETL incremental

**Contexto:** Hoy hay 58/58 tests pero no cubren `ModelBootstrap.run()` ni `IncrementalETL.run()` end-to-end — solo sus helpers.

**Por qué:** Un cambio al parser de CSVs, al binning o al filtro de madurez puede romper el pipeline silenciosamente y solo se detecta en ejecución manual. Un test con fixtures in-memory (SQLite + DataFrames mini) cerraría esa brecha.

**Criterio de hecho:** Tests en `tests/` que ejecuten `ModelBootstrap.run()` e `IncrementalETL.run()` contra una DB SQLite `:memory:` con CSVs sintéticos de `<=100` filas, verificando conteos y unique constraints.

---

## 3. `FACT_METRICS_HISTORY` BI-friendly

**Contexto:** La tabla requiere 3 JOINs para obtener una fila legible (`MetaMetricThresholds` para `metric_name`, `MetaModelRegistry` para `submodel_id`, `MetaVariables` para `variable_name`). Además: el nombre del target vive embebido en `metric_name` (ej: `gini_b_malo8_13`) y `origination_week` de Gini/KS queda enterrado en el JSON `details`.

**Por qué:** Conectar Power BI / Tableau directamente a la tabla actual obligaría a replicar los JOINs y parsear JSON en cada dashboard. Bloquea el consumo directo.

**Criterio de hecho:** Columnas denormalizadas `metric_name`, `target_name`, `segment_id`, `origination_week` en `FACT_METRICS_HISTORY`; ADR nueva documentando la denormalización; notebook de validación ajustado si aplica.

---

## 4. Desacoplar `metric_id` de SCD2 de thresholds

**Contexto:** `FactMetricsHistory.metric_id` es FK a `MetaMetricThresholds.id`. Cuando un umbral se versiona (SCD2), el nuevo threshold tiene un `id` distinto; el `UniqueConstraint(model_registry_id, calculation_week, metric_id, variable_id)` trata el `id` nuevo como una métrica diferente y permite duplicados conceptuales.

**Por qué:** En BI, `GROUP BY metric_name` devuelve filas duplicadas salvo que se filtre por `valid_to IS NULL` en el JOIN. El acoplamiento mezcla **identidad de la métrica** (`metric_name`, estable) con **configuración del umbral** (versionada).

**Opciones:**
- (A) Tabla `META_METRICS` con `metric_name` como PK estable; FK desde `FactMetricsHistory` a ella en vez de a `MetaMetricThresholds`.
- (B) Columna `metric_name` denormalizada en `FactMetricsHistory` como parte del unique constraint.

**Criterio de hecho:** ADR que documente la opción elegida + migración del schema + validación de que `GROUP BY metric_name` no duplica tras un cambio de threshold.

---

## 5. CI con ambos perfiles de instalación

**Contexto:** `pyproject.toml` usa grupos de Poetry: `main` (ETL puro, 6 paquetes) y `pipeline` (opcional, 9 paquetes adicionales). La separación es clave para desplegar imágenes mínimas por job (ver `decisions.md §8.2.15` superseded + §8.2.19).

**Por qué:** Hoy no hay CI que verifique que `poetry install --only main` alcanza para ejecutar bootstrap + ETL. Un import accidental desde `mlmonitor.data.*` hacia `mlmonitor.report.*` (grupo `pipeline`) pasaría desapercibido hasta el deploy.

**Criterio de hecho:** Workflow que corra dos jobs: (a) `poetry install --only main` + `python -c "from mlmonitor.data import bootstrap, incremental_etl"` + tests que no dependen de `pipeline`; (b) `poetry install --with pipeline` + `poetry run pytest` completo.

---

## 6. Persistencia del Gini/KS global (`FACT_GLOBAL_METRICS`)

**Contexto:** En la iteración 2 (2026-05-10) se agregó al PDF la sección "Métricas Globales por Target" — Gini/KS/n_obs computados sobre la población combinada de todos los segmentos para una `origination_week` (= `calculation_week - lag`). Hoy se calcula al vuelo en `ReportBuilder.build()` vía `get_gini_ks_global()` y se descarta tras renderizar. No se persiste porque `FactMetricsHistory.model_registry_id` es `NOT NULL` y no encaja semánticamente "global = sin segmento".

**Por qué:** Sin persistencia no hay histórico semanal del global. Para tendencias agregadas (¿el modelo entero está degradándose?) toca recalcular desde `FACT_PERFORMANCE_INDIVIDUAL` cada vez, y los datos crudos pueden purgarse antes de que se hagan los análisis. También bloquea poner el global como serie en dashboards.

**Opciones:**
- (A) Nueva tabla `FACT_GLOBAL_METRICS` con `(model_id, calculation_week, target_variable)` como unique. Columnas: `gini, ks, auc, n_obs, origination_week`. Pro: separación limpia entre métricas por-segmento y agregadas. Contra: una tabla más para mantener.
- (B) Hacer `FactMetricsHistory.model_registry_id` nullable y usar `NULL` para "global". Pro: una sola tabla, queries uniformes. Contra: rompe convenciones existentes y filtros por segmento necesitan `WHERE model_registry_id IS NOT NULL`.

**Criterio de hecho:** ADR documentando la opción elegida, migración del schema, calculator (o builder) persiste el global tras computarlo, builder lo lee de DB en lugar de recomputarlo, test que valide idempotencia (re-run no duplica), entrada en `data_model.md` describiendo la nueva tabla/columna. Nota: AUC es derivable de Gini (`AUC = (Gini + 1) / 2`), no hace falta persistirlo aparte; mantenerlo solo en render del PDF.
