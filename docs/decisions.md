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


## §8.2.21 Contrato de env vars en `entrypoint.sh` + script de backfill local

**Fecha:** 2026-04-27 · **Estado:** Aceptado · **Supersede:** —

**Contexto.** El MVP en ECS Fargate (§8.2.20) auto-detecta la semana de ejecución desde los CSVs sincronizados de S3. Para operación recurrente surgen tres necesidades que no resolvía el `entrypoint.sh` original: (a) re-correr una semana específica (RUN_DATE), (b) regenerar solo PDF si los datos ya están en RDS (SKIP_ETL), (c) suprimir correo o LLM en pruebas (NO_EMAIL, NO_LLM). Adicionalmente, el backfill histórico de `FACT_METRICS_HISTORY` requiere correr ETL+pipeline de muchas semanas — una operación one-shot que no encaja en Fargate y conviene ejecutar desde laptop.

**Decisión.**
1. `docker/entrypoint.sh` lee 4 env vars opcionales: `RUN_DATE` (forza `--date` en ambos scripts), `SKIP_ETL=1`, `NO_EMAIL=1`, `NO_LLM=1`. Sin override, comportamiento idéntico a §8.2.20.
2. Disparo ad-hoc desde CLI: `aws ecs run-task ... --overrides ` con `containerOverrides[0].environment`.
3. Backfill histórico: `scripts/backfill.py` itera lunes ISO entre `--start` y `--end` y llama `run_incremental_etl.py` + `run_pipeline.py --no-email --no-llm` por subprocess. Inyecta `S3_BUCKET=""` en el environment para deshabilitar la subida del PDF (contrato de `config/settings.py`, ver CLAUDE.md §2). Los PDFs quedan en `artifacts/reports/` local; bórralos al terminar.
4. `SKIP_PIPELINE` **no** se implementa: no hay caso de uso real (ETL sin pipeline no produce métricas).

**Razones.**
- Override por env var (no por flag CLI) es el patrón natural en ECS — `--overrides` ya inyecta env, no requiere parsear args.
- Backfill como script Python (no bash loop): manejo de errores por semana, idempotente vía `UniqueConstraint` en FACT_*, simétrico con `run_pipeline.py`.
- Backfill desde laptop (no desde Fargate): es one-shot, no se quiere pagar warm-up de Fargate por iteración, y el `S3_BUCKET=""` evita ensuciar el bucket con N PDFs históricos que nadie va a leer.

**Alternativas descartadas.**
- Pasar `--date` como argumento posicional al contenedor: requeriría `command` override en run-task, menos compatible con el Scheduler que no inyecta args.
- Backfill desde ECS con N invocaciones del schedule: caro y más lento; sin necesidad operativa.

**Verificación.** Smoke test 2026-04-27 con task def `mlmonitor:2` y overrides `RUN_DATE=2026-01-05 SKIP_ETL=1 NO_EMAIL=1 NO_LLM=1`: exit 0, logs muestran las 4 env vars aplicadas, pipeline corrió en 14s (sin ETL, sin LLM, sin SES), PDF generado y subido a S3 (sin `S3_BUCKET` override en este caso).


## §8.2.22 Reemplazo del set de targets monitoreados

**Fecha:** 2026-04-27 · **Estado:** Aceptado · **Supersede:** —

**Contexto.** El bootstrap inicial declaraba `first_payment_default2` como target monitoreado (lag 2 semanas). Crédito confirma que ese target fue agregado por desconocimiento del equipo de monitoreo, no porque sea de interés operativo: ningún reporte ni alerta se basa en él. Por otro lado, los CSVs de origen (`muestra_weekly`, `base_train_test_bb`) tienen las columnas `b_malo14_26` y `b_malo14_52`, que sí son de interés pero no estaban en `META_VARIABLES`/`META_METRIC_THRESHOLDS` porque al inicio del proyecto los datos dummy no cubrían los lags 26/52.

**Decisión.**
1. Eliminar `first_payment_default2` por completo de la DB (META + FACT) sin SCD2-cerrar. No se preserva histórico: no hay valor analítico en él porque nunca fue una decisión operativa, sólo configuración mal copiada.
2. Agregar `b_malo14_26` (lag 26) y `b_malo14_52` (lag 52) como targets nuevos en `META_VARIABLES` (uno por segmento) y sus 6 thresholds globales (`gini_*`, `ks_*`, `ordering_violations_*`) en `META_METRIC_THRESHOLDS`.
3. Ajustes paralelos en código: `bootstrap.TARGET_VARIABLES`, default de `metric_type` en `metrics/performance.py`, columna FPD removida del HTML del reporte y del prompt del LLM (`analyst/prompts.py`), comentarios en `db/models.py` y `metrics/business_metrics.py`, `notebooks/README.md` (sección sobre cobertura de FPD eliminada).
4. Migración a RDS aplicada con `scripts/migrate_targets_2026_04_27.py` (idempotente, dry-run por defecto) — borra 11 META_VARIABLES, 3 META_METRIC_THRESHOLDS, 44 FACT_METRICS_HISTORY, 41 FACT_PERFORMANCE_BINNED, 25,952 FACT_PERFORMANCE_INDIVIDUAL referidas a `first_payment_default2`; inserta 22 META_VARIABLES (11 × 2 nuevos targets) y 6 thresholds.

**Razones.**
- Eliminar (vs SCD2-cerrar) refleja la intención: no es un retiro operativo de un target válido, es la corrección de una entrada que nunca debió existir. SCD2 está pensado para evolución legítima, no para borrar errores de configuración.
- `b_malo14_26` y `b_malo14_52`: pre-cargar en DB para que cuando los CSVs cubran historia suficiente (producción) el ETL los empiece a poblar automáticamente, sin requerir un cambio de schema en el momento. En desarrollo el ETL los detecta y reporta `no records` — no se rompe nada.
- Convención de `lag_semanas` para `b_malo<a>_<b>`: extremo superior (`b`) en todos los casos. **Corrigendum 2026-04-28**: `b_malo8_13` se cargó por error con `lag=8` (extremo inferior) en el bootstrap inicial. Crédito confirma que es un error: el lag siempre debe ser el extremo superior porque corresponde al tiempo necesario para observar el outcome completo de la ventana. Corregido a `lag_semanas=13` en `bootstrap.py` y aplicado a RDS con `scripts/migrate_lag_b_malo8_13_2026_04_28.py` (UPDATE en META_VARIABLES + DELETE de FACT_PERFORMANCE_BINNED/INDIVIDUAL de `b_malo8_13` para regenerar con la cohorte correcta).

**Alternativas descartadas.**
- SCD2-cerrar `first_payment_default2`: deja registros vivos pero con `valid_to` poblado y mantiene FACT histórico, contradice el espíritu de la corrección.
- Esperar a que llegue producción con lags 26/52 antes de dar de alta: requeriría una migración futura cuando los datos ya estén ingresados, con riesgo de inconsistencia entre semanas con/sin esos targets.

**Verificación.**
- Bootstrap fresh sobre SQLite local: 11 segmentos, 172 META_VARIABLES, 20 META_METRIC_THRESHOLDS (vs 17 antes), 755 META_BASELINE_DISTRIBUTIONS.
- ETL `--date 2026-01-05`: 24,404 individual rows; logs muestran "no records" para `b_malo14_26` y `b_malo14_52` (esperado en dummy).
- Pipeline `--no-email --no-llm`: 231 métricas, PDF generado.
- 58/58 tests pasan.
- RDS: dry-run post-migración reporta 0 filas a borrar/insertar (idempotente).


## §8.2.23 Thresholds per-segmento desde CSV de crédito

**Fecha:** 2026-04-27 · **Estado:** Aceptado · **Supersede:** —

**Contexto.** `META_METRIC_THRESHOLDS` se inicializaba con 20 thresholds **globales** (`model_registry_id IS NULL`) hardcodeados en `bootstrap.py`. Eran el mismo número para los 11 segmentos, sin calibración. Crédito entregó `data/inputs/raw_tables/tresholds_monitoreo.csv` con 356 filas que sí están calibradas por segmento (`bb_1..bb_11`) para `psi`, `null_rate`, gini/ks/ordering_violations de los 6 targets, y gini de variables de scorecard. El CSV tiene errores humanos: campo `direction` inconsistente, variables intermedias (`EXTRA_SERC`) mezcladas con las del scorecard, métrica `gini_INTERCEPTO` que no aplica, omisiones de targets en algunos segmentos.

**Decisión.**
1. Reemplazar los 20 thresholds globales por **315 per-segmento** (11 × ~28 métricas/segmento) derivados del CSV. `model_registry_id` siempre poblado; ya no hay fila global para psi/null_rate/targets/scorecard-vars.
2. Nuevo módulo `data/threshold_loader.py` encapsula las reglas de derivación y se reusa desde `bootstrap.py` (DBs nuevas) y desde `scripts/migrate_thresholds_2026_04_27.py` (DBs existentes).
3. **Direction canónica en código** (no del CSV): `psi/null_rate/ordering_violations` → `higher_worse`; `gini/ks` → `lower_worse`. El CSV se ignora en este campo por errores humanos detectados.
4. **Defaults cuando el CSV no trae la métrica esperada:** psi (0.10/0.20), null_rate (0.03/0.10), gini_target (0.35/0.25), ks_target (0.20/0.15), ord_target (1/2), gini_scorecard_var (0.15/0.05). El último es nuevo; consenso del CSV.
5. **Filtros del CSV:** ignorar `gini_INTERCEPTO`, ignorar variables `EXTRA_SERC` (intermedias, no scorecard), ignorar targets fuera de `TARGET_VARIABLES`. Variables de scorecard en SERC se mapean a canónico vía `serc_to_canonical` (ej. `gini_EDAD` → `gini_edad`).
6. **No preservar histórico de thresholds** (sin SCD2-close): `DELETE FROM META_METRIC_THRESHOLDS` y reinsertar. Los thresholds globales originales no tenían valor analítico — eran configuración temporal.
7. **Borrar también `FACT_METRICS_HISTORY`** entera: regenerable por backfill con los nuevos thresholds. Coherente con tirar el histórico de thresholds.
8. `AlertEvaluator` actualizado: `_metric_map` re-keyed a `(metric_name, model_registry_id)`; `get_metric_id` y los 6 call-sites en `metrics/calculator.py` pasan `model_registry_id`.

**Razones.**
- Per-segmento es el estándar de calibración del scorecard (cada `bb_<n>` tiene su propio comportamiento). Globales no reflejaban esa realidad.
- `direction` canónica en código blinda contra errores humanos en el CSV (que se detectaron) y deja una sola fuente de verdad.
- Defaults explícitos en código permiten que el bootstrap funcione aunque el CSV evolucione: si crédito agrega una variable nueva al scorecard pero olvida actualizar el CSV, el bootstrap no falla.
- Borrar histórico (vs SCD2-close) refleja la intención: los globales no eran un threshold "vigente" que se está retirando, eran un placeholder.

**Alternativas descartadas.**
- Mantener globales como fallback y solo agregar per-segmento como override: el bug del CSV (omisiones en algunos segmentos) haría que un segmento sin entrada caiga al global, ocultando que falta calibración. Mejor: insertar default explícito per-segmento, visible en la DB.
- Confiar en `direction` del CSV: errores detectados (gini con `higher_worse`, etc.). Costo de validar humano > costo de aplicar regla canónica.
- SCD2-cerrar globales antes de insertar per-segmento: agrega filas sin valor analítico (los globales no eran un estado operativo legítimo).

**Verificación.**
- 68/68 tests pasan (incluye 10 nuevos en `tests/test_threshold_loader.py`).
- Bootstrap fresh sobre SQLite: 315 thresholds, 0 globales, todos con `model_registry_id` poblado y `direction` canónica.
- Pipeline `--no-email --no-llm`: 231 métricas calculadas, PDF generado.
- RDS post-migración: 541 filas FACT_METRICS_HISTORY borradas + 20 globales borrados; 315 per-segmento insertados. Re-ejecución reporta "ya migrado" (idempotente).


## §8.2.24 Eliminar columna huérfana `MetaModelRegistry.lag_semanas`

**Fecha:** 2026-04-28 · **Estado:** Aceptado · **Supersede:** —

**Contexto.** `MetaModelRegistry.lag_semanas` (Integer, `default=8`) existía desde el diseño inicial pero nunca fue leída por ningún código de la aplicación. El `default=8` codificaba el lag erróneo viejo de `b_malo8_13` (corregido en ADR §8.2.22 corrigendum). El lag operativo vive en `MetaVariables.lag_semanas` (uno por target), que es donde semánticamente corresponde.

**Decisión.**
1. `ALTER TABLE META_MODEL_REGISTRY DROP COLUMN lag_semanas;` en RDS y SQLite.
2. Eliminar la columna de `db/models.py::MetaModelRegistry`.
3. Eliminar `lag_semanas=None` del `MetaModelRegistry(...)` en `bootstrap.py::_populate_meta_model_registry`.
4. Documentar en `data_model.md §2.1` que el lag operativo vive en `META_VARIABLES.lag_semanas`, no en el registry.

**Razones.**
- Semánticamente el lag pertenece al **target** (`META_VARIABLES`), no al modelo. Modelos con varios targets tienen N lags distintos (BazBoost: 6 targets con lags 4/6/13/16/26/52); un solo campo a nivel registry sería ambiguo. Aplica a cualquier paradigma de ML futuro: regresión lineal, XGBoost, time-series — el lag siempre es propiedad del outcome que se predice.
- La columna huérfana es **deuda activa**: cualquier código nuevo que tropiece con ella podría asumir incorrectamente que `MetaModelRegistry.lag_semanas` es la fuente de verdad y dispararse `default=8` (el bug que acabamos de corregir).
- YAGNI: si en el futuro un modelo nuevo necesita un horizonte temporal a nivel registry (escenario hipotético), se agrega una columna con nombre semántico claro (`prediction_horizon_weeks`?) en su momento. Mantener especulativamente una columna mal nombrada no es estrategia de extensibilidad.

**Alternativas descartadas.**
- Mantener la columna y cambiar `default=8` → `default=None`: deja código muerto en el schema sin valor agregado. La columna nunca se lee — el default no importa.
- Mantener la columna "por si futuros modelos la necesitan": especulación sin caso de uso concreto. Schema cleanup ahora vs. eventual schema redesign cuando emerja la necesidad real.

**Verificación.**
- 68/68 tests pasan.
- Bootstrap fresh sobre SQLite: schema sin la columna; pipeline end-to-end OK.
- RDS: `ALTER TABLE META_MODEL_REGISTRY DROP COLUMN lag_semanas` aplicado vía script one-shot. Idempotente (chequea `information_schema.columns`).


## §8.2.25 Reducir set operativo de targets de 6 a 3 (PROPUESTA — pendiente de aprobación)

**Fecha:** 2026-04-29 · **Estado:** Propuesto · **Supersede:** parte de §8.2.22 (alta de `b_malo14_52`)

**Contexto.** Tras §8.2.22 el set operativo quedó en 6 targets (`b_malo2_4`, `b_malo4_6`, `b_malo8_13`, `b_malo8_16`, `b_malo14_26`, `b_malo14_52`). En la práctica el negocio sólo evalúa **3 ventanas**: corta (`b_malo4_6`), media (`b_malo8_13` — primario actual) y larga (`b_malo14_26`). Las 3 restantes contaminan la sección "Metadata" y la tabla "Métricas de Negocio por Decil" del PDF semanal sin agregar valor analítico. Adicionalmente, `b_malo14_52` requiere lag de 52 semanas — irreal para detección temprana de drift.

**Decisión.**
1. **Código (ya aplicado en local, 2026-04-29):**
   - `data/bootstrap.py::TARGET_VARIABLES` reducido a 3 entradas: `b_malo4_6` (lag 6), `b_malo8_13` (lag 13), `b_malo14_26` (lag 26).
   - `analyst/prompts.py`, `report/templates/submodel_section.html`, `report/builder.py` ahora iteran dinámicamente sobre `performance_coverage` (sin hardcodes de targets).
   - `tests/test_threshold_loader.py` actualizado para usar el nuevo set; `expected_total` derivado de `len(TARGET_VARIABLES)`.
   - CSVs en `data/inputs/raw_tables/` siguen trayendo las 6 columnas de targets — el ETL los ignora defensivamente (no error, sólo log).
2. **RDS (pendiente de autorización del usuario):**
   ```sql
   UPDATE meta_variables
   SET valid_to = CURRENT_DATE
   WHERE variable_rol = 'target'
     AND variable_name IN ('b_malo2_4', 'b_malo8_16', 'b_malo14_52')
     AND valid_to IS NULL;
   ```
   Cierre SCD2 (no DELETE) para preservar auditoría: registros vivos pasan a inactivos con `valid_to` poblado. **No tocar** `FACT_PERFORMANCE_BINNED`, `FACT_PERFORMANCE_INDIVIDUAL`, `FACT_METRICS_HISTORY` — su histórico se mantiene íntegro por reglas de auditoría sobre append-only.
3. **Thresholds asociados** (`META_METRIC_THRESHOLDS` con `metric_name` ∈ `{gini_b_malo2_4, ks_b_malo2_4, ordering_violations_b_malo2_4, gini_b_malo8_16, ks_b_malo8_16, ordering_violations_b_malo8_16, gini_b_malo14_52, ks_b_malo14_52, ordering_violations_b_malo14_52}`): cerrar también vía `valid_to = CURRENT_DATE` por simetría con §8.2.23.
4. Script idempotente `scripts/migrate_targets_2026_04_29.py` (a redactar tras aprobación) — dry-run por defecto, `--apply` para ejecutar; chequea `valid_to IS NULL` para idempotencia.

**Razones.**
- **SCD2-cerrar (vs DELETE como en §8.2.22 con `first_payment_default2`):** los 3 targets descontinuados **sí fueron operativos** durante un periodo (entrada legítima en bootstrap, posibles cargas históricas en `FACT_*`). Eliminar perdería la trazabilidad de cuándo y por qué dejaron de monitorearse. `first_payment_default2` se eliminó porque nunca debió existir; estos se retiran porque el negocio decidió enfocarse en 3 ventanas.
- **Preservar `FACT_*`:** `FACT_PERFORMANCE_*` y `FACT_METRICS_HISTORY` son append-only por regla — su valor analítico (backfill, comparaciones históricas si en el futuro se reactiva un target) no debe perderse. Las consultas semanales filtran por joins con `META_VARIABLES.valid_to IS NULL`, así que con SCD2-cerrar los 3 targets ya no aparecerán en métricas nuevas.
- **CSVs intactos:** los archivos que entrega crédito siguen trayendo 6 columnas. El ETL los ignora dinámicamente (filtro por `META_VARIABLES` activos). Esto desacopla el ciclo de operaciones del agente del ciclo de regeneración de extracts.

**Alternativas descartadas.**
- **DELETE puro de los registros activos en `META_VARIABLES`:** rompe FK soft de `FACT_*` con histórico (queries de auditoría que reconstruyen el estado a fecha X fallarían). Va contra la convención SCD2 del schema.
- **Mantener los 6 y filtrar sólo en el reporte:** el costo de mantener thresholds, baselines y métricas calculadas para 3 targets que el negocio no mira es alto — más volumen en `FACT_METRICS_HISTORY`, más ruido en alertas, más prompts al LLM. Mejor excluirlos del cálculo desde la fuente.
- **Borrar también `FACT_*` históricos (estilo §8.2.23):** §8.2.23 borró `FACT_METRICS_HISTORY` porque los thresholds globales no eran "vigentes" sino placeholders. Aquí los datos históricos sí son operativamente válidos para el periodo en que se calcularon — eliminar perdería información real.

**Verificación esperada (post-apply en RDS).**
- `SELECT COUNT(*) FROM meta_variables WHERE variable_rol='target' AND valid_to IS NULL;` → 33 (3 targets × 11 segmentos), antes 66.
- `SELECT COUNT(*) FROM meta_variables WHERE variable_rol='target' AND valid_to = CURRENT_DATE;` → 33 (los recién cerrados).
- `SELECT COUNT(*) FROM fact_performance_binned;` y `fact_metrics_history`: sin cambios.
- Pipeline `--date <lunes>` post-migración: PDF muestra 3 columnas en "Métricas de Negocio por Decil" y 3 filas en "Performance" de Metadata.
- Dry-run del script one-shot post-migración: 0 cambios (idempotente).

**Pendiente para aplicar.**
- Aprobación explícita del usuario.
- Redacción del script `scripts/migrate_targets_2026_04_29.py`.
- Ejecución manual (no automatizada) sobre RDS.


## §8.2.26 Gráficas de deciles reales en el PDF (matplotlib + base64 inline)

**Fecha:** 2026-04-30 · **Estado:** Aceptado · **Supersede:** —

**Contexto.** La sección `<h2>Métricas de Negocio por Decil</h2>` en `report/templates/submodel_section.html` mostraba en realidad **bines de ancho fijo de 100 puntos** (`0-100`, `100-200`, ..., `900-1000`) definidos en `data/bootstrap.py::SCORE_BINS` y persistidos en `META_VARIABLES.binning_rules`. El nombre era engañoso: los bines fijos no son percentiles y no garantizan 10% de población por bin (segmentos con score concentrado pueden tener bines vacíos o sobre-poblados). Para auditoría y comparación entre cohortes/targets se requería una visualización con **deciles reales** (qcut sobre `fnpuntaje` continuo) más una representación gráfica que la tabla de números actual no comunica de forma directa.

**Decisión.**
1. **Renombrar** la sección actual a `Métricas de negocio por bin de score (ancho fijo)` para reflejar fielmente su semántica.
2. **Agregar una nueva sección** `Métricas de negocio por decil` debajo, con dos gráficas matplotlib **por segmento**:
   - **Consolidada:** una figura con barras de igual altura (10% de población por decil) y N líneas de tasa de impago superpuestas, una por cada target activo cuyo `lag_semanas <= primary.lag_semanas`. La cohorte usada es la del target primario (`calculation_week - primary.lag`). Targets con lag mayor se omiten silenciosamente y se listan en una nota inferior porque sobre esa cohorte aún no son observables.
   - **Por target (subplots):** una figura con N paneles horizontales, uno por cada target activo, cada uno con su **propia cohorte madura** (`calculation_week - target.lag`). Si una cohorte no tiene datos o `n < DECILE_MIN_OBS = 100`, el panel muestra un placeholder en gris.
3. **Decil 1 = scores más bajos** (mayor riesgo). NO se invierte el score (a diferencia de Gini/KS en `metrics/performance.py:179`); el eje X habla por sí solo y la línea de tasa debe verse decreciente con decil creciente.
4. **Embedding:** matplotlib backend `Agg` (headless, ECS-compatible) → buffer PNG @ 150 dpi → base64 → `<img src="data:image/png;base64,...">` inline en el HTML. Sin archivos intermedios.
5. **Schema de transporte:** nuevo campo `decile_charts: dict` en `SegmentMetrics` (`analyst/base.py`) con keys `consolidated` y `per_target`, cada una con `{img_b64, available, reason, cohort_week, ...}`. El `img_b64` se almacena **sin** el prefijo `data:image/png;base64,` — el template lo concatena. Esto facilita tests (`base64.b64decode` directo) y mantiene el dato puro.
6. **Módulos nuevos:**
   - `metrics/decile_metrics.py`: `compute_decile_table(scores, flags)` con `pd.qcut(..., duplicates="drop")` y `get_decile_data_for_segment(...)` que carga `FACT_PERFORMANCE_INDIVIDUAL` por target y arma los dos payloads. Para la consolidada hace `merge` por `credito_id` para garantizar que los deciles definidos sobre la cohorte primaria se aplican consistentemente al flag de cada target elegible.
   - `report/charts.py`: `render_consolidated_decile_chart(...)` y `render_per_target_decile_chart(...)`. Paleta consistente con `styles.css` (`#1a1a3e`, `#06b6d4`, `#8b5cf6`, `#ef4444`).
7. **Builder:** la resolución de `resolved_primary_target` se movió **antes** del loop de segmentos (antes se calculaba después) para poder pasarla a `_build_segment_metrics`. La generación de gráficas se delega en `_build_decile_charts` que se llama tras `business_table`.

**Razones.**
- **Deciles reales > bines fijos** para análisis de discriminación: cada decil aporta el mismo peso poblacional y permite comparar cohortes/targets de forma calibrada. Los bines fijos siguen siendo útiles como vista compatible con el scorecard original (rangos de score interpretables por negocio), por eso ambos coexisten.
- **Cohorte primaria para la consolidada (no por target):** permite contrastar varios targets sobre la misma población — la pregunta operativa es "¿cómo se comporta esta cohorte en sus distintas ventanas observables?", no "¿cómo se ve cada cohorte por separado?" (eso es la gráfica B).
- **Regla `lag <= primary.lag` en la consolidada:** sobre la cohorte de hace `primary.lag` semanas solo son observables los targets cuya maduración cabe en ese tiempo. Incluir b_malo14_26 con lag 26 cuando primary es b_malo8_13 (lag 13) requeriría una cohorte de hace 26 semanas — distinta de la primaria — y rompería la unicidad de la población por decil. Los targets omitidos se anuncian explícitamente al lector.
- **Base64 inline vs archivos:** ECS Fargate no tiene `/tmp` persistente y weasyprint admite data URIs nativo. Coste: PDF ~10× más pesado (1.87 MB vs ~150 KB para 22 PNGs), aceptable. Evita gestión de paths relativos y limpieza de artefactos intermedios.
- **No invertir score para deciles:** Gini/KS invierten en `_build_performance_df_individual` para que el ranking quede ascending-by-risk (convención de scoring). En deciles, el eje X ya identifica decil 1 = riesgo alto; invertir confundiría al lector que conoce el rango natural del score [0, score_max]. Trade-off: la línea de tasa decrece (lo opuesto a la curva ROC mental), pero el usuario lo pidió explícito y es la convención común en análisis crediticio (decil 1 = peor calidad).
- **Una gráfica B por segmento (no global):** la calibración de cada submodelo (s1..s11) es distinta — un decil global mezclaría poblaciones con scoring scales heterogéneos. La granularidad por segmento es coherente con el resto del PDF.

**Alternativas descartadas.**
- **Renombrar sin agregar gráfica:** dejaría el problema semántico resuelto pero no aportaría la visualización solicitada por el usuario. Descartada.
- **Una sola gráfica (sólo consolidada o sólo per-target):** la consolidada permite comparar targets sobre una misma población; la per-target permite ver cada cohorte madura por separado. Cada una responde una pregunta distinta y son complementarias. El usuario aceptó el costo visual de tener ambas.
- **SVG inline en lugar de PNG base64:** SVG es vectorial pero pesa más en HTML grandes y weasyprint tiene casos borde con SVG anidado; PNG @ 150 dpi es suficiente para A4.
- **Guardar PNGs en `artifacts/reports/img/`:** requeriría gestión de paths relativos, limpieza tras render, y preocupaciones de concurrencia en ECS. Base64 los hace agnósticos al filesystem.
- **Caching/reuso del DataFrame entre gráficas:** el costo medido es ≤3 s para 22 figuras en 11 segmentos. No vale la complejidad de un cache hasta que se demuestre cuello de botella.
- **Configurar `DECILE_MIN_OBS` por segmento vía `META_METRIC_THRESHOLDS`:** prematuro. Hardcode a 100 cubre el caso actual; mover a tabla cuando un segmento legítimamente lo necesite.

**Verificación.**
- 75/75 tests pasan (suite previa 68/68 + `tests/test_decile_metrics.py` con 6 unit + 1 smoke PNG-signature).
- Pipeline contra SQLite local (`DB_URL=sqlite:///mlmonitor_dev.db AWS_PROFILE=nonexistent_xyz S3_BUCKET= poetry run python scripts/run_pipeline.py --date 2026-02-02 --no-email --no-llm`) → PDF de 1.87 MB en `artifacts/reports/mlmonitor_2026-02-02.pdf` (vs ~150 KB previo, consistente con 22 PNGs nuevos).
- Sección renombrada visible; nueva sección con consolidada (cohorte primaria) y per-target (subplots con cohortes propias) presente; nota de targets omitidos cuando aplica; placeholders cuando no hay cohorte madura.


## §8.2.27 PSI y null_rate con ventana rodante de 4 semanas

**Fecha:** 2026-05-04 · **Estado:** Aceptado · **Supersede:** —

**Contexto.** El cálculo original de `metrics/psi.py` comparaba la distribución de **una sola semana** de producción (`FACT_DISTRIBUTIONS WHERE origination_week = current_week`) contra el baseline de entrenamiento. Una sola semana tiene varianza alta por efectos de calendario (puentes, quincenas, eventos comerciales), por volumen bajo en algunos segmentos, y por ruido idiosincrático del scoring batch. Eso producía oscilaciones de PSI semana-a-semana que disparaban alertas WARNING/CRITICAL no atribuibles a drift real, contaminando la narrativa del LLM y el correo semanal. Lo mismo aplicaba a `null_rate`, que también vivía sobre una sola semana.

**Decisión.**
1. **Ventana rodante de 4 semanas** para PSI y para `null_rate`: el cálculo agrega `current_week + las 3 anteriores` (lunes ISO contiguos). Constante `PSI_WINDOW_WEEKS = 4` parametrizable por argumento opcional `window_weeks` en las funciones públicas, por si en el futuro se necesita ajustar.
2. **Agregación por suma de conteos crudos**, no por promedio de porcentajes. Para cada `bin_label` de la variable, el helper `_aggregate_distributions_over_window(...)` hace `SUM(bin_count) GROUP BY bin_label` filtrando `origination_week IN (window)` y luego renormaliza. Esto equivale a calcular el PSI sobre la población combinada de las 4 semanas, respetando el peso real de cada semana.
3. **Null rate análogo:** `Σ null_count / Σ total_records` sobre la ventana. `total_records` se duplica por bin en `FACT_DISTRIBUTIONS` (una fila por bin), así que se agrega por `(variable_id, origination_week)` con `MAX` antes de sumar entre semanas — evita inflarlo por el número de bins.
4. **Cobertura parcial permitida:** si la ventana cubre menos semanas (al inicio del histórico, primeras semanas tras un baseline refresh, huecos por fallos de ETL), se usa lo que existe en `FACT_DISTRIBUTIONS` sin rellenar con ceros. No se loguea warning explícito — la query simplemente trae menos filas. Caso límite con 1 semana = comportamiento equivalente al cálculo single-week previo, lo que preserva los tests de integración existentes.
5. **Firma pública estable:** `get_psi_for_variable`, `get_psi_for_all_variables`, `get_null_rates` mantienen sus parámetros previos; sólo se añadió `window_weeks` como kwarg opcional con default `PSI_WINDOW_WEEKS`. `MetricsCalculator` no requiere cambios — sigue invocándolas igual.
6. **`compute_psi_from_df` intacta:** la función pura que calcula PSI a partir de dos DataFrames no cambia; toda la lógica nueva vive en el helper de agregación. Los tests unitarios sobre `compute_psi_from_df` siguen siendo válidos.

**Razones.**
- **Suma de conteos > promedio de porcentajes:** estadísticamente correcto. Promediar `bin_percentage` semana-a-semana asume volúmenes iguales y, si una semana tiene 100 registros y otra 10 000, le da el mismo peso. La suma de `bin_count` es equivalente a calcular PSI sobre la unión de las 4 cohortes — interpretación consistente con cómo se construyó el baseline.
- **4 semanas:** ventana mensual usual en monitoreo crediticio. Cubre un ciclo de quincenas y eventos calendario habituales sin entrar en territorio donde drift real legítimo se diluye demasiado. Si hay drift real persistente, una semana tiene 25% del peso y la métrica reacciona sin retraso excesivo (vs un EMA o una ventana trimestral).
- **Cobertura parcial sin warning:** en operación normal (post-MVP estable) la ventana siempre estará completa. En transitorios (primer mes tras un baseline refresh, recuperación de un hueco de ETL) la métrica degrada suavemente al equivalente single-week. Decisión consciente: preferir un valor calculable sobre lo que existe que devolver `0.0` o `NaN` — la ausencia de alarma sería peor que una alarma sobre menos historia.
- **Aplicar a `null_rate` también:** mismo problema de varianza semana-a-semana. Coherencia narrativa con PSI: si se afirma que la métrica de drift está sobre una ventana mensual, no tiene sentido que la de calidad de datos sea single-week.

**Alternativas descartadas.**
- **Promedio simple de PSI semanales (4 PSIs y media):** matemáticamente incorrecto. PSI no es lineal en la distribución; la media de PSIs no es el PSI de la población combinada y puede sobrestimar drift cuando una semana tiene un blip aislado.
- **EMA (exponential moving average):** suaviza también pero introduce un parámetro `α` adicional sin ganancia clara para el usuario y dificulta interpretar el "qué semanas estoy mirando". Una ventana fija de 4 lunes ISO es trivial de explicar.
- **Ventana de 8 ó 13 semanas:** atenúa demasiado drift real. Crédito típicamente itera campañas mensualmente; una ventana mensual responde a tiempo a cambios de mix.
- **Recalcular `bin_percentage` y persistir agregado en una nueva tabla:** sobre-ingeniería. La agregación es barata (SUM sobre ≤4 × n_bins filas por variable por query) y se hace una sola vez por ejecución del pipeline. No hay caso de uso que justifique materializarla.
- **Reemplazar la firma pública (p.ej. añadir `window_weeks` como argumento posicional obligatorio):** rompería invocaciones existentes en `metrics/calculator.py`. Default opcional preserva compatibilidad y permite probar otras ventanas en notebooks.

**Verificación.**
- 81/81 tests pasan (75 previos + 6 nuevos en `tests/test_psi.py::TestRollingWindow`):
  - `test_window_helper_returns_descending_mondays` — el helper devuelve los lunes correctos.
  - `test_partial_coverage_falls_back_to_existing_weeks` — con sólo current_week en DB, mismo PSI que single-week previo.
  - `test_full_window_smooths_single_week_spike` — con 3 semanas previas estables, el PSI rodante sobre una serie totalmente estable se mantiene bajo umbral.
  - `test_drift_spike_attenuated_by_stable_history` — un spike aislado en current_week se atenúa cuando se agrega historia previa estable.
  - `test_aggregation_sums_bin_counts` — verifica que el helper suma `bin_count` y no promedia porcentajes (test con totales muy distintos: 4000 vs 1000).
  - `test_null_rate_uses_rolling_window` — null_rate con 200 nulls / 4000 totales = 0.05 vs 0.20 single-week.
- Pipeline `--date 2026-04-06 --no-email --no-llm` (semana 15 ISO 2026): ETL re-ingesta y pipeline recalcula con la nueva métrica. Resultado documentado en `devlog.md`.


## §8.2.28 Deciles con ventana rodante de 4 semanas + persistencia en `FACT_DECILES_HISTORY`

**Fecha:** 2026-05-04 · **Estado:** Aceptado · **Supersede:** §8.2.26 (parcialmente — los gráficos siguen, pero la cohorte deja de ser puntual)

**Contexto.** Las gráficas de deciles introducidas en §8.2.26 calculaban la tabla decil-a-decil sobre **una sola semana** (`primary_cohort = calculation_week - target_lag`) leyendo `FACT_PERFORMANCE_INDIVIDUAL`. Heredan el mismo problema que tenía PSI antes de §8.2.27: una sola semana presenta volumen bajo en varios segmentos (sub-scorecards `s1`, `s2` con cohortes < 200 obs), `pd.qcut(q=10)` colapsa a menos deciles, y la tasa por decil oscila por ruido de cohorte pequeña. Además, los deciles no se persistían en DB — eran efímeros, recomputados cada vez que se rendereaba el PDF, sin histórico consultable para análisis longitudinal del ordering del scorecard.

**Decisión.**
1. **Ventana rodante de 4 semanas hacia atrás** desde el cohorte primario para la consolidada y desde cada cohorte específico para el bloque per-target. Constante `DECILE_WINDOW_WEEKS = 4` y helper `_window_weeks(cohort_end)` que devuelve `[cohort_end, cohort_end-1, cohort_end-2, cohort_end-3]` lunes ISO, análogo exacto a `psi.py::_window_weeks`. El filtro pasa de `origination_week == cohort` a `origination_week.in_(window)`. La función pura `compute_decile_table` no cambia: opera sobre el DataFrame concatenado.
2. **Hacia atrás, no hacia adelante ni centrada:** las semanas posteriores al cohorte tienen créditos con `origination > calculation_week - lag` y por tanto **no han alcanzado madurez** para el target. Sus outcomes serían parciales o sesgados (créditos que ya cayeron en mora se observan; los que aún no, no). Las semanas anteriores tienen créditos *más* maduros que el lag exigido — sus outcomes son al menos tan confiables como los del cohorte central.
3. **Nueva tabla `FACT_DECILES_HISTORY`** en `db/models.py`. Columnas: `model_registry_id`, `calculation_week`, `target_variable`, `cohort_window_start/end`, `decile`, `score_min/max/mean`, `n_obs`, `n_events`, `event_rate`, `pct_population`. Unique constraint sobre `(model_registry_id, calculation_week, target_variable, decile)`. Append-only.
4. **Persistencia idempotente** vía helper `persist_deciles_history()` en `metrics/decile_metrics.py`: borra filas previas para `(model, calculation_week, targets_con_datos)` y luego inserta. Permite re-runs del pipeline sobre la misma semana sin duplicar y reflejando datos actualizados (correcciones del CSV upstream, cambios de fórmula). Se invoca desde `report/builder.py::_build_decile_charts` después de computar deciles. El builder del PDF sigue consumiendo el mismo dict computado en memoria — la persistencia es side-effect.
5. **Solo el bloque per-target se persiste**, no la consolidada. Razón: la consolidada es derivada (cada target tiene su tabla individual + las tasas cruzadas se reconstruyen merged-en-memoria si se necesita reproducir el chart). Persistir solo per-target evita redundancia y mantiene el unique constraint simple.

**Razones.**
- **Consistencia con §8.2.27:** mismo problema (varianza por cohorte chica), misma solución (ventana mensual hacia atrás). Coherencia narrativa: si PSI mira un mes, los deciles también deben mirar un mes.
- **Ventana de 4 semanas:** mismo balance de §8.2.27. Cubre ciclo de quincenas, atenúa ruido sin diluir drift real.
- **Persistir, no efímero:** patrón establecido del proyecto — `FACT_METRICS_HISTORY` ya guarda PSI/Gini/KS. Que los deciles fueran la única métrica efímera era una asimetría arbitraria. El valor concreto de persistir es habilitar análisis longitudinal del *ordering* (¿la curva event_rate-por-decil se está aplanando? ¿los deciles superiores aumentan tasa antes que los inferiores?) sin recomputar todo el pipeline.
- **Tabla nueva, no extensión de `FACT_METRICS_HISTORY`:** la métrica decil tiene granularidad distinta (10 filas por target × segmento × semana, vs 1 valor escalar). Forzarla en la tabla genérica long-format con `metric_name = event_rate_d1, event_rate_d2, ...` ensucia el catálogo de métricas y obliga al builder a parsear nombres. Tabla dedicada es más limpia.
- **Idempotencia por delete-then-insert (no UPSERT):** SQLite no tiene UPSERT trivial cross-dialect; delete-then-insert dentro de la misma transacción es robusto en SQLite y Postgres. La unique constraint actúa como red de seguridad si dos pipelines corren en paralelo (no es el caso operativo, pero defensivo).

**Alternativas descartadas.**
- **Cohorte puntual + ampliar `DECILE_MIN_OBS`:** subir el mínimo de obs a 400 dejaría a varios segmentos chicos (`s1`, `s2`) sin gráfica disponible. La ventana rodante mantiene la cobertura.
- **Ventana centrada o hacia adelante:** rompe la garantía de madurez del target. Inaceptable.
- **Persistir consolidada + per_target:** doble fuente de verdad, redundante. La consolidada es una proyección computacional de las tablas individuales.
- **Persistir como JSON blob en `FACT_METRICS_HISTORY.details`:** dificulta queries SQL del histórico (decil 1 a lo largo del tiempo requiere parsear JSON). Tabla relacional pura es mejor.

**Verificación.**
- 100/100 tests pasan (94 previos + 6 nuevos en `tests/test_decile_metrics.py`):
  - `TestWindowWeeks` × 3: el helper devuelve 4 lunes ISO descendentes, primer elemento = `cohort_end`, tamaño parametrizable.
  - `TestRollingWindowDeciles::test_window_aggregates_4_weeks_and_excludes_future` — inserta 100 obs/semana en 4 semanas hacia atrás + 1 semana hacia adelante; verifica que `n_obs.sum() == 400` (la futura es excluida) y que `cohort_window_start/end` quedan correctos.
  - `TestRollingWindowDeciles::test_persist_deciles_history_idempotent` — corre `persist_deciles_history` dos veces, verifica que el conteo de filas no crece.
- Pipeline `--date 2026-04-06 --no-email --no-llm` contra DB local: `FACT_DECILES_HISTORY` queda con 3 targets × 11 segmentos × 10 deciles = 330 filas, ventanas alineadas (`b_malo4_6` lag=6 → `2026-02-02..2026-02-23`; `b_malo14_26` lag=26 → `2025-09-15..2025-10-06`). PDF se genera correctamente; gráficas reflejan ~4× más obs por decil que con cohorte puntual.


## §8.2.29 Baseline derivado de `variables_serc` (primeras 4 semanas del año en curso)

**Fecha:** 2026-05-06 · **Estado:** Aceptado · **Supersede:** §8.2.16 y §8.2.18 (sólo la fuente del baseline; el schema `META_BASELINE_DISTRIBUTIONS` y la separación baseline/producción que esas ADRs introdujeron se mantienen sin cambios)

**Contexto.** §8.2.16 y §8.2.18 establecieron `base_train_test_bb.csv` (formato WIDE, ~501K filas) como fuente del baseline de PSI. La motivación entonces fue evitar la primera semana de `variables_serc` por volumen bajo y por requerir mapeo SERC→canónico. Con el sistema en producción y varios meses de datos acumulados en `variables_serc`, el contexto cambió: (a) ya hay volumen suficiente acumulando varias semanas recientes (la ventana W1-W4 2026 tiene 235 364 créditos únicos vs ~50K/segmento del baseline original); (b) `base_train_test_bb.csv` representa la población **de entrenamiento** del scorecard, no la población operativa actual — los segmentos en producción ya divergieron naturalmente de esa distribución por evolución del mix de canales, campañas y perfil de solicitantes; (c) ese drift estructural se contabilizaba como "alarma de PSI" en cada semana a pesar de no reflejar drift relativo a la operación reciente. Resultado: PSI sistemáticamente alto contra el baseline de entrenamiento, contaminando la lectura de "drift de la semana actual vs operación reciente", que es lo que el reporte semanal busca comunicar.

**Decisión.**
1. **Fuente del baseline = `variables_serc_*.csv`**, filtrado a las **primeras 4 semanas ISO del año en curso** (default 2026, lunes ISO `2025-12-29`, `2026-01-05`, `2026-01-12`, `2026-01-19`). El año y la cantidad de semanas son parametrizables (`--year`, `--n-weeks`) para soportar refrescos anuales del baseline o experimentación con ventanas distintas.
2. **Implementación como subclase, no reemplazo destructivo:** `src/mlmonitor/data/bootstrap_v2.py` define `ModelBootstrapV2(ModelBootstrap)` que sobreescribe sólo `_populate_baseline_distributions` y agrega un método de carga LONG (`_load_serc_baseline_window`). META_MODEL_REGISTRY, META_VARIABLES y META_METRIC_THRESHOLDS se heredan sin cambios. Runner paralelo `scripts/run_bootstrap_v2.py`. `bootstrap.py` y `run_bootstrap.py` originales se conservan operacionales — la transición a V2 como camino oficial se hace cambiando la invocación del bootstrap, no eliminando el código antiguo (deuda de consolidación registrada para otra iteración).
3. **Lógica de binning idéntica:** numéricas via `pd.qcut(q=10)` con cuts persistidos en `MetaVariables.binning_rules`; categóricas (fisexo) por `value_counts` persistido en `woe_categories`; score con `SCORE_BIN_CUTS` fijos (0-100, 100-200, …, 900-1000). Esto garantiza que las distribuciones de producción sigan binning-eando contra los mismos cuts que el baseline calculó, manteniendo la comparabilidad.
4. **Schema `META_BASELINE_DISTRIBUTIONS` sin cambios.** La tabla, su unique constraint `(model_registry_id, variable_id, bin_label)` y la separación lógica baseline/producción de §8.2.18 siguen siendo correctos — sólo cambia el origen de los datos que la pueblan.
5. **Ventana fija (no rolling) y refresco manual.** El baseline V2 no se actualiza automáticamente cada semana; se recalcula sólo cuando se ejecuta `run_bootstrap_v2.py`. Esto preserva la propiedad esencial del baseline: ser un punto de referencia estable contra el cual medir drift. Si fuera rolling, el "drift contra el baseline" tendería a cero por construcción.

**Razones.**
- **Misma fuente que producción.** El baseline y las semanas operativas ahora vienen del mismo extract, con los mismos filtros upstream, mismo formato LONG, mismo proceso de `serc_to_canonical`. Cualquier cambio sistemático en el pipeline upstream afecta a ambos por igual y no se contabiliza falsamente como drift.
- **Volumen suficiente.** 4 semanas × ~58K créditos = 235K obs total, distribuidos en 11 segmentos. Comparable en orden de magnitud al baseline original por segmento (~50K), eliminando la objeción de §8.2.16 sobre "primera semana muy pequeña".
- **Temporalidad cercana a la operación.** El baseline ahora representa "cómo se ve la operación al inicio del año" y el reporte mide "cómo se desvía de eso". Es una pregunta más útil operacionalmente que "cómo se desvía de la población de entrenamiento del scorecard hace X meses".
- **Refrescable anualmente sin cambios de código.** Cuando inicie 2027, se puede recorrer `--year 2027` y opcionalmente cerrar SCD2 del baseline anterior (decisión a discutir en su momento). El parámetro evita hardcodear el año.
- **Subclase sobre fork:** mantiene un único punto de verdad para META tables, thresholds y descripciones. Si bootstrap original cambia (p. ej. carga de descripciones desde diccionarios), V2 lo hereda sin esfuerzo.

**Alternativas descartadas.**
- **Mantener `base_train_test_bb.csv`:** la objeción operacional es real y persistente (drift estructural no relacionado con la semana actual). Soluciona algo que ya no es problema (volumen) a costa de algo que sí lo es (alarmas falsas).
- **Baseline rolling de N semanas que se actualiza cada semana:** elimina por construcción el concepto de "drift". Útil para detectar cambios bruscos vs el mes anterior, pero no es la pregunta que el reporte responde — y además se solapa con la ventana rodante de PSI introducida en §8.2.27.
- **Baseline como percentiles fijos (sin recálculo desde data):** sobre-ingeniería — la fuente real son los créditos del Q1 y derivar la distribución desde ellos es lo natural. Persistir percentiles "fijos" introduciría una capa de abstracción innecesaria.
- **Ventana de 1 ó 8 semanas en lugar de 4:** 1 semana reproduce el problema original de §8.2.16 (volumen bajo en algunos segmentos). 8 semanas empieza a solapar con periodos donde podría haber drift legítimo del año en curso, mezclando "punto de referencia" con "inicio del periodo monitoreado". 4 semanas = mes calendario, balance simétrico con la ventana rodante de PSI (§8.2.27).
- **Reescribir `bootstrap.py` directamente en lugar de subclasear:** dejaría sin paso atrás si V2 resultaba peor en producción. Coexistencia de ambos durante la transición es barata.

**Impacto medible.**
- Pipeline `--date 2026-04-06` con baseline original: `1 OK | 3 WARNING | 7 CRITICAL`.
- Pipeline `--date 2026-04-06` con baseline V2: `1 OK | 6 WARNING | 4 CRITICAL`.
- 3 segmentos pasaron CRITICAL → WARNING. La caída de severidad es esperada por construcción y consistente con la hipótesis: el drift contra una distribución reciente de la misma fuente es menor que contra la distribución de entrenamiento.

**Verificación.**
- Bootstrap V2 contra SQLite local: 11 segmentos · 139 META_VARIABLES · 216 META_METRIC_THRESHOLDS · 808 META_BASELINE_DISTRIBUTIONS. Ventana cargada: 2.83M filas SERC, 235 364 créditos únicos.
- ETL incremental para las 4 semanas operativas (2026-03-16, 03-23, 03-30, 04-06) corre OK; pipeline `run_pipeline.py --date 2026-04-06 --db-url sqlite:///mlmonitor_dev.db --no-email` genera 322 métricas y PDF sin errores.
- Tests existentes (no se agregaron tests nuevos en esta iteración — la lógica de binning es la heredada y ya estaba cubierta): suite total se ejecuta como `poetry run pytest` y debe seguir verde tras la migración a V2 oficial.

**Pendiente / próximos pasos.**
- Consolidar `bootstrap.py` y `bootstrap_v2.py` en un único bootstrap cuando V2 esté validado en RDS (deuda registrada).
- Decidir cadencia de refresco (anual? bianual?) y mecanismo (¿cerrar SCD2 del baseline anterior y crear uno nuevo, o sobrescribir?). Esto se documentará en una ADR aparte cuando se acerque el primer refresco.
