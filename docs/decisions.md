# DECISIONS.md — MLMonitor ETL Refactor

Registro de decisiones arquitectónicas (ADR). Solo contiene decisiones que explican el **por qué** del estado actual del código — no es bitácora (ver [`../devlog.md`](../devlog.md)), ni backlog (ver [`backlog.md`](./backlog.md)), ni referencia de schema (ver [`architecture/data_model.md`](./architecture/data_model.md)).

---

## 8.2 Decisiones técnicas

### 8.2.1 Separación bootstrap vs. ETL incremental

**Decisión:** Dividir `raw_etl.py` en dos módulos: `bootstrap.py` (tablas META, una vez) y `incremental_etl.py` (tablas FACT, semanal).

**Por qué:** El ETL monolítico mezclaba inicialización (constantes del modelo, umbrales) con carga recurrente (distribuciones, performance). Esto impedía:
- Ejecutar el ETL sin re-crear META.
- Verificar idempotencia por semana.
- Agregar nuevos segmentos o variables sin re-procesar todo.

**Trade-off:** El bootstrap debe ejecutarse antes del primer ETL incremental. Si se modifica la estructura de META (nuevo target, nuevo segmento), hay que re-ejecutar el bootstrap y re-inicializar la DB.

### 8.2.2 Dos flujos independientes en el ETL

**Decisión:** Flow A (distribuciones por scoring week) y Flow B (performance por disbursement week) son independientes y se ejecutan en la misma invocación del ETL.

**Por qué:**
- Flow A necesita el CSV de `variables_serc` filtrado por la semana de ejecución W.
- Flow B necesita el CSV de `muestra_weekly` filtrado por `semana_num = iso_week(W - lag)` para cada target.
- Ambos flujos tienen distintos DataFrames de entrada y distintas tablas de destino.

**Trade-off:** Ambos flujos se ejecutan aunque uno no tenga datos. El overhead es mínimo (un query EXISTS por flujo).

### 8.2.3 Tipo Date para origination_week/execution_week en FactPerformanceIndividual

**Decisión:** Cambiar `origination_week` y `execution_week` de `Integer` (ISO week como 202541) a `Date` en `FactPerformanceIndividual`.

**Por qué:**
- `FactPerformanceBinned` ya usaba `Date`. La inconsistencia obligaba a conversiones ISO→Date en el cálculo de Gini/KS.
- Con `Date`, los queries de `performance.py` y `calculator.py` usan directamente `origination_week = execution_week - timedelta(weeks=lag)` sin conversión.

**Trade-off:** Requiere re-inicialización de la DB (DROP + CREATE). Aceptable porque el nuevo bootstrap ya contempla esto.

### 8.2.4 Eliminación de `semanas_vida`

**Decisión:** Eliminar la columna `semanas_vida` de `FactPerformanceIndividual`.

**Por qué:** El campo era redundante y su cálculo tenía un bug: `semana_num - vintage` medía el desfase solicitud→surtimiento, no la edad del crédito. La madurez ahora se garantiza por diseño: el ETL filtra `semana_num = iso_week(W - lag)`, por lo que solo trae créditos surtidos hace exactamente `lag` semanas.

### 8.2.5 Gini/KS desde datos individuales

**Decisión:** Calcular Gini/KS desde `FACT_PERFORMANCE_INDIVIDUAL` (un row por crédito) en lugar de `FACT_PERFORMANCE_BINNED` (agregado por decil de score).

**Por qué:** El cálculo por bins introduce error de discretización. Con datos individuales, las curvas de Lorenz y las distribuciones acumuladas son exactas. La función `compute_gini_ks_individual()` reemplaza a `compute_gini_ks()` para el pipeline principal, pero ambas se conservan.

**Trade-off:** Más rows leídos de la DB por cada cálculo de Gini/KS (~28K vs 10 rows). En SQLite es imperceptible. En PostgreSQL con 1M+ créditos por cohorte, podría necesitar un índice compuesto `(model_registry_id, origination_week, ventana)`.

### 8.2.6 Bin edges persistidos en META_VARIABLES

**Decisión:** Los bin edges de variables numéricas y los score bin cuts se almacenan en `META_VARIABLES.binning_rules` (JSON). El ETL incremental los lee de la DB.

**Por qué:** Evita hardcodear constantes en el código del ETL. Si los bins cambian (nuevo modelo, recalibración), solo se actualiza la DB via bootstrap.

### 8.2.7 Idempotencia por EXISTS check

**Decisión:** Antes de insertar rows para un (segmento, target, semana), se verifica con `SELECT 1 FROM ... WHERE ... LIMIT 1`. Si existe, se skip.

**Por qué:** Es O(1) por (segmento, semana) en vez de O(n) por fila individual. Permite re-ejecutar el ETL sin duplicar datos.

### 8.2.8 Alineación de fechas ETL ↔ calculator

**Decisión:** El ETL almacena `origination_week = execution_week - timedelta(weeks=lag)` (aritmética de calendario), no `_semana_to_date(iso_week)` (conversión W-MON).

**Por qué:** `calculator.py` computa `origination_week = current_week - timedelta(weeks=lag)`. Si el ETL usara la conversión ISO→W-MON, las fechas diferirían por hasta 6 días y los queries de Gini/KS no encontrarían datos. Al usar la misma aritmética, las fechas son idénticas.

### 8.2.9 Rename `reference_week` → `origination_week` en FACT_DISTRIBUTIONS

**Decisión:** Renombrar la columna `reference_week` a `origination_week` en `FactDistributions`.

**Por qué:** El nombre `reference_week` era confuso — sonaba como si identificara la distribución de referencia/baseline, pero esa responsabilidad la tiene `reference_flag`. El campo realmente almacena la semana de originación/scoring del crédito. Renombrar a `origination_week` unifica la nomenclatura con `FACT_PERFORMANCE_BINNED` y `FACT_PERFORMANCE_INDIVIDUAL`.

**Nota semántica:** En performance tables, `origination_week` = disbursement week (semana_num). En distributions, `origination_week` = scoring week (fdregistro_solicitud). Son eventos distintos pero típicamente la misma semana para un crédito dado.

### 8.2.10 Semana de ejecución derivada de `semana_observacion`

**Decisión:** El ETL incremental auto-detecta la semana de ejecución (W) desde `MAX(semana_observacion)` en `muestra_weekly`, y el pipeline auto-detecta su semana desde `MAX(origination_week)` en `FACT_DISTRIBUTIONS`.

**Por qué:** `semana_observacion` es la semana en que se evaluaron los outcomes — es el techo natural para W. Si W > semana_observacion, los targets podrían estar en 0 no porque el crédito esté al corriente sino porque la evaluación no ha ocurrido aún. Auto-detectar desde la data garantiza que solo se procesen outcomes confiables. `--date` queda como override para backfill y testing.

### 8.2.11 Eliminación de `raw_etl.py`

**Decisión:** Eliminar `raw_etl.py` y `scripts/init_db.py`.

**Por qué:** `bootstrap.py` + `incremental_etl.py` cubren toda la funcionalidad. `raw_etl.py` era incompatible con el schema actual (Integer→Date, semanas_vida eliminado) y ya no servía como referencia útil.

### 8.2.12 Corrección de alineación de fechas: W-MON → ISO Monday

**Decisión:** Reemplazar la derivación de `_origination_week` basada en `pd.to_period("W-MON")` por `date.fromisocalendar()` (Lunes ISO) en `bootstrap.py` e `incremental_etl.py`.

**Por qué:** Se detectó un desfase constante de **6 días** entre las dos convenciones de semana usadas en el sistema:
- `detect_execution_week()` usaba `date.fromisocalendar(year, week, 1)` → siempre **Lunes**
- `_origination_week` usaba `.to_period("W-MON").start_time.date()` → siempre **Martes** (W-MON = week ending Monday; start_time es el Martes previo)

Consecuencia: cuando se usaba auto-detección (sin `--date`):
1. Flow A producía 0 distribuciones (el filtro `_origination_week == execution_week` comparaba Martes con Lunes → nunca matcheaba)
2. Gini/KS/ordering_violations retornaban None/0 (el pipeline auto-detectaba `calculation_date` como Martes desde distribuciones, pero performance se almacenó con Lunes)

El bug no se detectó porque las pruebas end-to-end siempre usaron `--date` con fechas manuales. Los resultados reportados ("semana 2025-08-05" para Flow A, "semana 2025-10-13" para Flow B) eran de runs separados con fechas que coincidían con cada convención por separado.

**Fix aplicado:** Ambas rutas ahora usan `date.fromisocalendar(year, week, 1)` → Lunes ISO. Verificado empíricamente: todos los días de cualquier semana ISO producen el mismo Lunes.

**Trade-off:** Requiere re-ejecutar bootstrap y ETL incremental para que las distribuciones almacenadas usen Lunes. Los datos previos con Martes en `origination_week` no matchearán con el nuevo esquema. Re-inicialización de DB necesaria.

### 8.2.13 Último bin de score incluye el límite superior (score_max)

**Decisión:** El último bin de score (`900-1000` para BAZBOOST_V1) ahora usa `<=` en vez de `<` para el límite superior, incluyendo créditos con score exactamente igual a `score_max`.

**Por qué:** Con `scores < 1000`, un crédito con score = 1000 quedaba fuera de todos los bins:
- Excluido de `FACT_DISTRIBUTIONS` → PSI no lo contaba
- Excluido de `FACT_PERFORMANCE_BINNED` → no contribuía a ordering violations ni business table
- Presente en `FACT_PERFORMANCE_INDIVIDUAL` → sí contribuía a Gini/KS

Esto generaba una inconsistencia entre métricas binned e individuales. El impacto numérico es mínimo (scores de exactamente `score_max` son raros) pero el fix es trivial.

**Implementación:**
- `_bin_score` y `_reference_score_distributions`: el último bin usa `scores <= hi` en vez de `scores < hi`
- Flow B (`_flow_b_performance`): `pd.cut` se alimenta con `cut_edges[-1] + 1` para que el intervalo `[900, 1001)` incluya 1000, manteniendo el label `900-1000`

### 8.2.14 `score_max` parametrizado desde META_MODEL_REGISTRY

**Decisión:** La inversión de score para Gini/KS ahora lee `score_max` desde `MetaModelRegistry` en vez de usar `1000` hardcodeado.

**Por qué:** El cálculo de Gini/KS invierte el score (`score_max - fnpuntaje`) para que "alto riesgo" corresponda a "valor alto" en la curva ROC. Con `1000` hardcodeado, un modelo futuro con rango 0-100 o 0-850 produciría inversiones incorrectas y métricas de discriminación erróneas.

**Implementación:**
- `_build_performance_df()`, `_build_performance_df_individual()`, `get_gini_ks_for_segment()`: nuevo parámetro `score_max: int = 1000`
- `_calculate_segment_metrics()`: recibe `score_max` y lo pasa a `get_gini_ks_for_segment()`
- `run_for_model()`: lee `seg.score_max or 1000` del `MetaModelRegistry` y lo propaga

**Trade-off:** El default es 1000 para mantener backward compatibility. Los tests existentes (que usan `score_max=1000` implícitamente) siguen pasando sin cambios.

### 8.2.15 Separación ETL (VM) y Pipeline (Cloud) — superseded por §8.2.19

> **Estado:** obsoleta en su parte operativa. La separación de dependencias (grupos Poetry `main` / `pipeline`) y la parametrización CSV **siguen vigentes**; el modelo de ejecución VM + sync manual **quedó descartado** (ver §8.2.19). Se conserva por trazabilidad del origen de los grupos de Poetry.

**Decisión original:** Separar el proyecto en dos unidades de ejecución independientes dentro del mismo repo: ETL (corre en VM on-premise con dependencias mínimas) y Pipeline (corre en AWS ECS con dependencias completas).

**Por qué:**
- La VM on-premise donde viven los CSVs raw no puede instalar WeasyPrint/Cairo (dependencias de sistema) ni acceder a servicios AWS (Bedrock, S3, SES).
- El entorno Cloud (ECS) no tiene acceso al filesystem de la VM con CSVs.
- Ambos comparten el mismo modelo relacional (`models.py`) y la misma BD PostgreSQL (RDS).

**Cambios implementados (todos siguen vigentes salvo el sync VM→RDS):**

1. **Config:** `s3_bucket` default cambiado de `"ml-monitoring-reports-credito"` a `""` (vacío = S3 upload deshabilitado). `secrets_loader` ya importa boto3 lazily; la falla se captura en `_build_settings()` y usa defaults.
2. **Parametrización CSV:** nombres hardcodeados reemplazados con auto-detección por glob + args CLI explícitos `--serc-file` / `--weekly-file` en `run_bootstrap.py` y `run_incremental_etl.py`. Corrige bug de case sensitivity (`s32` vs `S32`) que fallaría en Linux.
3. **Aislamiento de imports:** ETL importa solo de `mlmonitor.data.*` y `mlmonitor.db.*`. Pipeline importa de `mlmonitor.pipeline.*`, `mlmonitor.metrics.*`, `mlmonitor.report.*`, etc. Sin imports cruzados.
4. **Separación de dependencias:** `pyproject.toml` usa grupos de Poetry 2.x: dependencias core (ETL) siempre instaladas; dependencias de pipeline en grupo opcional `pipeline`.
5. **Dead code eliminado:** `src/mlmonitor/etl/` (skeleton ABCs sin imports) eliminado.
6. **PostgreSQL local:** `docker-compose.yml` con PostgreSQL 16 para pruebas de separación.

**Lo que queda obsoleto (§8.2.19):** el flujo VM → sync manual → RDS → Pipeline en ECS. Hoy todo corre contra RDS desde local y se migrará completo a AWS.

### 8.2.16 Baseline de entrenamiento como artefacto de referencia

**Decisión:** Usar el archivo `base_train_test_bb.csv` (formato WIDE, `data/inputs/raw_tables/`) como referencia de distribuciones del modelo BAZBOOST_V1 frente a producción, en lugar de la primera semana de `variables_serc`.

**Por qué:** volumen estable (~501K filas vs cientos en la primera semana) y nombres canónicos ya resueltos como columnas directas, lo que elimina la necesidad de mapear SERC→canónico al construir el baseline. Los detalles de estructura (columnas, dimensiones, targets disponibles) viven en [`architecture/data_model.md §2.4`](./architecture/data_model.md#24-meta_baseline_distributions).

La implementación de esta decisión está en §8.2.18.

### 8.2.17 Notebook de validación de métricas

**Decisión:** Mantener `notebooks/validacion_metricas_baseline.ipynb` como *ground truth* manual: calcula PSI, Gini y KS desde CSVs raw sin importar `mlmonitor` y compara contra `FACT_METRICS_HISTORY`.

**Por qué:** un cambio en el cálculo (binning, inversión de score, filtro de madurez) podría pasar los tests unitarios pero divergir numéricamente del valor esperado. El notebook cierra esa brecha con un cómputo independiente. El detalle de secciones, hallazgos numéricos (71/71 PSI, 33/33 Gini, 33/33 KS) y observaciones vive en [`../notebooks/README.md`](../notebooks/README.md).

### 8.2.18 Refactor: baseline de entrenamiento como referencia de PSI

**Decisión:** Reemplazar la primera semana de `variables_serc` como referencia de PSI por el baseline de entrenamiento (`base_train_test_bb.csv`). Crear tabla separada `META_BASELINE_DISTRIBUTIONS` y eliminar `reference_flag` de `FACT_DISTRIBUTIONS`.

**Motivación:**
- La primera semana de producción no aproxima la distribución de entrenamiento: `n_ref` puede ser muy bajo por segmento, generando PSIs inflados (documentado en `notebooks/README.md`: media `|diff|` ~2.64 vs baseline).
- El baseline tiene ~501K filas → bins estables; la primera semana podía tener solo cientos de registros por segmento.
- El baseline de entrenamiento es conceptualmente un artefacto distinto de las semanas incrementales de producción; mezclarlos en la misma tabla con un flag era semánticamente incorrecto.

**Cambios de schema:**
- **Nueva tabla `META_BASELINE_DISTRIBUTIONS`:** `(model_registry_id, variable_id, bin_label)` como unique constraint. Columnas: `bin_count`, `bin_percentage`, `null_count`, `total_records`, `loaded_at`. Sin `origination_week` (no aplica).
- **`FACT_DISTRIBUTIONS`:** eliminada columna `reference_flag`. Solo contiene datos de producción (semanas incrementales).
- **`bin_percentage` redundante:** se guarda `bin_percentage = bin_count / total_records` como campo derivado redundante para evitar el cómputo en cada query de PSI (patrón read-heavy, write-once). Ambos campos se calculan juntos al insertar y nunca se actualizan por separado.

**Cambios de código:**
- **`bootstrap.py`:** lee `base_train_test_bb.csv` (formato WIDE) en lugar de `variables_serc` (formato LONG). Variables canónicas son columnas directas del CSV; no requiere mapeo SERC→canónico. Bin edges (quantiles) se computan desde el baseline (~50K obs/segmento vs ~200 de la primera semana). Parámetros eliminados: `serc_filename`, `weekly_filename`, `reference_week_override`. Nuevo: `baseline_filename`.
- **`psi.py`:** lee referencia desde `META_BASELINE_DISTRIBUTIONS` en lugar de `FACT_DISTRIBUTIONS` con `reference_flag=1`.
- **`incremental_etl.py`:** eliminadas todas las referencias a `reference_flag`.
- **`orchestrator.py`:** auto-detect de `calculation_date` ya no filtra por `reference_flag`.
- **`run_bootstrap.py`:** nuevo arg `--baseline-file`; eliminados `--serc-file`, `--weekly-file`, `--reference-week`.

**Compatibilidad futura:** el bootstrap acepta un DataFrame para el baseline, por lo que cuando se conecte a BD en la VM solo cambia cómo se obtiene el DataFrame, no la lógica de binning/distribución.

### 8.2.19 Ejecución completa en AWS — descarte de la arquitectura VM on-premise

**Decisión:** Todo el ciclo (ETL y Pipeline) correrá en AWS. Se descarta la separación "ETL en VM on-premise + Pipeline en AWS" planteada en §8.2.15.

**Contexto (abril 2026):**
- §8.2.15 asumía que los CSVs raw vivían en una VM on-premise incapaz de alojar WeasyPrint/Cairo y sin acceso a servicios AWS, por lo que se propuso correr el ETL en la VM y sincronizar META/FACT a RDS manualmente.
- Durante la validación se determinó que las restricciones de la VM impiden sostener el ETL de forma operativa, y que la estrategia de sync manual VM → RDS agrega fragilidad sin beneficio claro.
- Los servicios AWS (RDS, Bedrock, SES, Secrets Manager) ya están activos y se consumen hoy desde local para validación end-to-end.

**Consecuencias:**
- Se elimina la fase de sync VM → RDS. El ETL escribirá directamente sobre la misma BD (RDS) que lee el Pipeline.
- La plataforma de ejecución en AWS (ECS Fargate, Step Functions + Batch, Lambda, otra) queda **pendiente de definir** como próximo hito.
- La fuente de los CSVs raw en producción queda **pendiente** (ver `../dudas_documentacion.md` D5): podrían vivir en S3, en una BD origen externa, o depositarse vía un pipeline upstream.
- Los grupos de Poetry (`main` para ETL, `pipeline` opcional) **siguen vigentes**: permiten construir imágenes distintas por job aunque todas corran en AWS. La separación de imports (`mlmonitor.data.*` + `mlmonitor.db.*` para ETL, vs. `mlmonitor.pipeline.*` / `metrics.*` / `report.*` / `analyst.*` para Pipeline) se mantiene para poder desplegar cada job con sus dependencias mínimas.
- El `docker-compose.yml` con PostgreSQL local sigue siendo útil para validar la separación de dependencias antes de desplegar a AWS.

**Supersede:** §8.2.15 queda **obsoleta** en la parte operativa (la separación de dependencias y la parametrización CSV siguen siendo válidas, pero el modelo de ejecución VM + sync manual no aplica). §8.2.19 es la fuente de verdad.

**Trade-offs:**
- Se pierde la opción de mantener los CSVs cerrados dentro de la red on-premise. Cualquier flujo productivo implica mover los CSVs (o sus datos) a AWS.
- La automatización del disparo semanal (cron/EventBridge, SLA, responsable operativo) aún no está definida.

**Estado:** decisión aceptada en abril 2026. Pendiente: (a) elegir plataforma AWS concreta, (b) definir origen de CSVs raw en producción, (c) automatizar el disparo semanal.


### 8.2.20 Plataforma de ejecución en AWS: ECS Fargate + EventBridge Scheduler (MVP)

**Fecha:** 2026-04-23
**Estado:** aceptada
**Supersede:** completa §8.2.19 (que dejó la plataforma pendiente de definir).

**Decisión:** el ciclo completo (ETL + métricas + reporte + publicación) corre como una **sola task de ECS Fargate**, disparada semanalmente por **EventBridge Scheduler**. La imagen vive en ECR. Los CSVs semanales se suben manualmente a `s3://ml-monitoring-reports-credito/inputs/raw_tables/`; el contenedor hace `aws s3 sync` al arrancar.

**Razones:**
- El flujo es lineal (sync → ETL → Pipeline). Step Functions agregaría complejidad sin beneficio.
- WeasyPrint requiere libs nativas (Cairo, Pango, GDK-PixBuf). Lambda obligaría a imagen de contenedor igual que Fargate, perdiendo la simplicidad de Lambda puro.
- El tiempo de ejecución con Bedrock y 11 segmentos ronda 3–5 min, margen cómodo respecto al timeout 15 min de Lambda pero sin mucha cabecera. Fargate no tiene ese techo.
- La imagen de Fargate es la misma que se testea localmente con `docker run`, lo cual simplifica debugging.
- Disparo manual (`aws ecs run-task`) sigue disponible y no depende del Scheduler.

**Alternativas descartadas:**
- Lambda + Step Functions + EventBridge: por lo anterior.
- AWS Batch: overhead operativo para un job que corre <10 min una vez por semana.

**Separación ETL/Pipeline (§8.2.15) formalmente cerrada.** Hoy corren en la misma task secuencialmente. Los grupos de Poetry (`main` y `pipeline`) siguen útiles como guía de imports, pero no se despliegan por separado: una sola imagen con el grupo `pipeline` completo.

**Recursos creados (cuenta `930067561911`, `us-east-1`):**
- ECR: `930067561911.dkr.ecr.us-east-1.amazonaws.com/mlmonitor` (tags `v0.1.0`, `latest`).
- ECS cluster: `mlmonitor-cluster`; task definition `mlmonitor:1` (CPU 1024, memoria 4096, X86_64).
- IAM: `mlmonitor-ecs-execution` (managed `AmazonECSTaskExecutionRolePolicy`), `mlmonitor-task` (inline: Secrets, Bedrock, S3 read inputs / write reports, SES), `mlmonitor-scheduler-invoke` (ECS RunTask + PassRole).
- SG: `sg-0c54b54ed399b471c` (`mlmonitor-fargate-sg`, solo egress) en VPC default.
- Log group: `/ecs/mlmonitor` (retención 30 días).
- EventBridge Schedule: `mlmonitor-weekly`, cron `0 14 ? * MON *` UTC = lunes 08:00 CDMX.

**Consecuencias y deuda técnica:**
- `sg-02e9d008b587402f7` (SG de RDS) sigue abierto `0.0.0.0/0:5432`. Pendiente cerrar al SG de Fargate.
- SES en sandbox. Pendiente salir.
- Terraform sin escribir. Siguiente paso, con `terraform import`.
- CI/CD (build + push) sin automatizar.
- RDS `PubliclyAccessible=true` en VPC default con subnets públicas. Mover a subnets privadas + NAT en fase de hardening.

**Verificación:** smoke test `aws ecs run-task` el 2026-04-23: exit 0, PDF publicado en `s3://ml-monitoring-reports-credito/mlmonitor/reports/mlmonitor_2026-01-05.pdf`, correo entregado a `samsalriu@gmail.com` desde `1206029@onuriscp.com`, duración ~3:25.
