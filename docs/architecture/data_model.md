# Modelo de datos — MLMonitor

Documentación del schema relacional, su semántica y las reglas de negocio que lo sustentan. Para el "qué" estructural, la fuente canónica es `src/mlmonitor/db/models.py` (SQLAlchemy 2.0). Este documento responde el **por qué**.

> El modelo de datos está **congelado** tras la validación del notebook `notebooks/validacion_metricas_baseline.ipynb` (71/71 PSI, 33/33 Gini, 33/33 KS coinciden con cálculo manual). Cualquier cambio requiere autorización explícita del usuario (ver `CLAUDE.md §3`).

---

## 0. Datos raw y contexto de negocio

### 0.1 Modelo monitoreado

**BAZBOOST_V1** es un scorecard de regresión logística para crédito, con 11 segmentos (`s1`–`s11`) agrupados en 5 grupos (G1–G5). Cada segmento tiene su propio conjunto de variables input (entre 3 y 15 variables), un score de salida (0–1000, invertido: bajo score = alto riesgo), y 5 variables target con distintos lags de maduración.

### 0.2 Variables target y lags

| Target         | lag_semanas | Semántica                               |
|----------------|-------------|-----------------------------------------|
| `b_malo2_4`    | 4           | Malo a 2–4 semanas                      |
| `b_malo4_6`    | 6           | Malo a 4–6 semanas                      |
| `b_malo8_13`   | 13          | Malo a 8–13 semanas                     |
| `b_malo8_16`   | 16          | Malo a 8–16 semanas                     |
| `b_malo14_26`  | 26          | Malo a 14–26 semanas (ventana media)    |
| `b_malo14_52`  | 52          | Malo a 14–52 semanas (ventana larga)    |

### 0.3 Columnas de la data raw

#### `variables_serc` (CSV)

Detalle de variables por solicitud de score. Cada fila es una (solicitud, variable).

| Columna                | Tipo     | Descripción                                                                 |
|------------------------|----------|-----------------------------------------------------------------------------|
| `fiidscoreds`          | int      | ID único de la solicitud de score (clave del crédito)                       |
| `fiidsegmento`         | int      | Segmento del scorecard (1–11)                                              |
| `fnpuntaje`            | float    | Score total del scorecard (0–1000; bajo = alto riesgo)                      |
| `fcnombre_variable`    | string   | Nombre SERC de la variable (se mapea a nombre canónico vía `variable_mapping.py`) |
| `fcvalor_variable`     | string   | Valor de la variable. Numérico o categórico; `-100` = sentinel para missing |
| `fdregistro_solicitud` | int (ms) | Timestamp de registro de la solicitud (epoch en milisegundos). Se deriva a `_reference_week` (semana de scoreo, periodo W-MON) en el código |

**Nota:** `_reference_week` no existe en el CSV — es una columna derivada que `bootstrap.py` e `incremental_etl.py` crean a partir de `fdregistro_solicitud`: `timestamp → to_period("W-MON") → start_time.date()`.

#### `muestra_weekly` (CSV)

Muestra semanal de créditos con outcomes y score. Cada fila es un crédito en una semana de surtimiento.

| Columna                  | Tipo   | Descripción                                                         |
|--------------------------|--------|---------------------------------------------------------------------|
| `fiidscoreds`            | int    | ID único de la solicitud de score (join key con `variables_serc`)   |
| `fiidsegmento`           | int    | Segmento del scorecard (1–11)                                      |
| `fnpuntaje`              | float  | Score total del scorecard                                           |
| `semana_num`             | int    | **Semana de surtimiento** (disbursement week) como entero ISO (ej: `202536`). Coincide con la semana ISO de `fdfechasurt` cuando esa fecha existe. |
| `semana_sol`             | int    | *(Opcional en algunos extracts.)* Semana ISO de **solicitud**; alineada con `fdfecsol` y con `vintage` en formato decimal. |
| `vintage`                | float  | Semana de solicitud/registro en formato decimal (ej. `2025.36`). Cuando existen `semana_sol` / `fdfecsol`, coincide con ellos. **No** sustituye a `semana_num` para la lógica de madurez del ETL (esa usa surtimiento). |
| `vintage_bis`            | float  | Semana de surtimiento en formato decimal; equivalente a `semana_num` cuando ambas columnas existen. |
| `flg_baz_boost`          | int    | 1 = crédito scoreado por BazBoost; 0 = otro modelo. Si el extract es 100 % BazBoost y la columna no viene del origen, **añadirla** como constante `1` para no cambiar el filtro del ETL. |
| `flg_surtida`            | int    | 1 = crédito efectivamente surtido (disbursed); 0 = no surtido       |
| `b_malo2_4`              | int    | Target: malo a 2–4 semanas (lag 4). 0/1                            |
| `b_malo4_6`              | int    | Target: malo a 4–6 semanas (lag 6). 0/1                            |
| `b_malo8_13`             | int    | Target: malo a 8–13 semanas (lag 13). 0/1                          |
| `b_malo8_16`             | int    | Target: malo a 8–16 semanas (lag 16). 0/1                          |
| `b_malo14_26`            | int    | Target: malo a 14–26 semanas (lag 26). 0/1                         |
| `b_malo14_52`            | int    | Target: malo a 14–52 semanas (lag 52). 0/1                         |
| `semana_observacion`     | int    | **Semana de evaluación de outcomes** (entero ISO, ej: `202602`). Valor único por extract — es la fecha del snapshot. Los targets solo son confiables hasta esta semana. Un crédito surtido en semana S con target de lag L solo tiene outcome confiable si `semana_observacion >= S + L` |

**Nota operativa (extracts upstream):**

- Si el archivo trae la columna `semana` en lugar de `semana_num`, es la misma semántica: semana ISO de **surtimiento** (equivalente a la semana de `fdfechasurt`). Renombrar a `semana_num` antes de correr el ETL si el código no hace el alias.
- `semana_observacion` suele **no** venir del warehouse: quien genera el extract debe agregarla con el **mismo** entero ISO en todas las filas — la semana en que se construyó el snapshot (techo hasta donde los targets son observables).
- Los targets `b_malo8_16`, `b_malo14_26`, `b_malo14_52` pueden faltar en extracts parciales; el ETL omite esos targets con warning (comportamiento esperado). Los demás targets se calculan si la columna existe.

**Nota dev — lags 26 y 52:** los targets `b_malo14_26` y `b_malo14_52` existen como columnas del CSV pero su ETL no produce filas con los datos dummy actuales (semanas 32–41 de 2025): la cohorte madura requeriría origen 26 ó 52 semanas atrás de la semana de ejecución, que cae fuera del rango del dummy. Aparecerán cuando los CSVs cubran historia suficiente.

**Filtros del ETL:** Solo se procesan créditos con `flg_baz_boost = 1` y `flg_surtida = 1`.

**Lógica temporal:** Para un target con lag L y semana de ejecución W, el ETL filtra `semana_num = iso_week(W - L)`, obteniendo créditos surtidos hace exactamente L semanas. La madurez se garantiza por este filtro — no se calcula `semanas_vida`. La semana de ejecución W no debe exceder `MAX(semana_observacion)` para garantizar que los outcomes estén completamente observados.

### 0.4 Convención de fechas

- Los CSVs usan **semana ISO como entero** (ej: `202541` = semana 41 de 2025).
- Las tablas FACT almacenan **`date`** (tipo Date de SQL).
- `origination_week` = fecha de surtimiento del crédito (disbursement week), derivada de `semana_num` en `muestra_weekly`.
- `execution_week` = fecha de la semana en que se ejecuta el ETL/pipeline.
- La aritmética `origination_week = execution_week - timedelta(weeks=lag)` es consistente entre `incremental_etl.py` y `calculator.py`.

### 0.5 Flujo de datos semanal

1. Cada semana llegan ~28K nuevos créditos scoreados (CSV `variables_serc`).
2. Cohortes previas maduran según el lag de cada target (CSV `muestra_weekly`).
3. El pipeline se ejecuta: bootstrap (una vez) → ETL incremental (semanal) → cálculo de métricas → reporte PDF.

---

## 1. Reglas de diseño transversales

### 1.1 META vs FACT

- **Tablas `META_*`:** catálogos versionados que describen qué modelo, variables, umbrales y baseline se están monitoreando. Siguen **SCD2**: nunca se sobreescriben; se cierra el registro vigente (`valid_to = fecha`) e inserta uno nuevo (`valid_from = fecha`, `valid_to = NULL` ⇒ registro activo).
- **Tablas `FACT_*`:** mediciones temporales, **append-only**. Cada fila es una observación inmutable. La idempotencia se garantiza por `UniqueConstraint` sobre la clave de negocio.

Razón: separar **configuración** (qué y cómo se mide) de **observación** (qué pasó). Cambios de umbral o de baseline no reescriben el histórico; se añade una versión nueva y se preserva la trazabilidad.

### 1.2 Append-only con `UniqueConstraint`

Todas las `FACT_*` tienen un `UniqueConstraint` sobre la combinación (segmento, semana, métrica/variable/target) que define la identidad de negocio. El ETL hace un `SELECT 1 … LIMIT 1` antes de insertar, por lo que **reejecutar el mismo ETL no duplica**. Ver decisions §8.2.7.

### 1.3 Tipo `JSONText`

`db/models.py::JSONText` es un `TypeDecorator` custom que serializa JSON sobre una columna `TEXT`. Se usa en `MetaVariables.binning_rules`, `MetaVariables.woe_categories` y `FactMetricsHistory.details`.

Razón: portabilidad entre dialectos. `JSONB` de Postgres y `JSON` de SQLite tienen APIs distintas; `TEXT + json.dumps/loads` funciona igual en SQLite, Postgres y Oracle. Hoy la BD productiva es Postgres (RDS), pero el schema se mantuvo dialecto-neutro por decisión temprana.

### 1.4 Surrogate keys

Las `FACT_*` apuntan a `META_*` por `model_registry_id`, `variable_id` y `metric_id` (FK a `META_MODEL_REGISTRY.id`, `META_VARIABLES.id`, `META_METRIC_THRESHOLDS.id`). Esto permite que el ETL calcule usando IDs integer rápidos sin depender de strings.

Consecuencia operativa (decisions §8.2.15): si alguna vez se mueven datos entre BDs, las tablas META y FACT deben copiarse juntas preservando los `id` originales, y luego ajustar las secuencias con `setval()`.

### 1.5 Fechas: `Date` alineado a Lunes ISO

Todas las columnas de semana en `FACT_*` son `Date` (tipo SQL), no `Integer`. El valor siempre corresponde al **Lunes ISO** de la semana (`date.fromisocalendar(y, w, 1)`).

Razón (decisions §8.2.12): hubo un bug histórico donde parte del código usaba `pd.to_period("W-MON")` (que produce Martes) y otra parte `date.fromisocalendar()` (Lunes). Resultado: 6 días de desfase que hacían que Flow A viera 0 distribuciones y Gini/KS retornaran `None` en modo auto-detect. Desde el fix, **solo se usa Lunes ISO**. Los CSVs de entrada aceptan el formato `YYYYWW` (entero ISO) y se convierten inmediatamente.

### 1.6 `origination_week` tiene dos semánticas según la tabla

Es la misma columna pero identifica eventos distintos:

- En `FACT_DISTRIBUTIONS`: **semana de scoreo** (scoring week). Cuándo se calculó el score para ese crédito.
- En `FACT_PERFORMANCE_BINNED` y `FACT_PERFORMANCE_INDIVIDUAL`: **semana de surtimiento** (disbursement week). Cuándo se desembolsó el crédito.

Típicamente ambas coinciden (se surte poco después de scorear), pero no son el mismo evento. Ver decisions §8.2.9 para la historia del rename (`reference_week` → `origination_week`).

### 1.7 `MISSING_SENTINEL = -100`

Valor reservado para nulos en variables numéricas del scorecard. No se confunde con `NaN` porque los CSVs raw pueden traer el string `"-100"` literal. Vive en `bootstrap.py` e `incremental_etl.py`.

### 1.8 Score invertido para Gini/KS

`inverted = score_max - score`. En el scorecard, **bajo score = alto riesgo**, pero las curvas ROC esperan que el "predictor alto" corresponda al evento positivo. La inversión es obligatoria para que Gini > 0 en un modelo discriminante.

`score_max` no está hardcodeado: se lee de `MetaModelRegistry.score_max` (1000 para BAZBOOST_V1) para soportar modelos con rangos distintos (ver decisions §8.2.14).

### 1.9 Madurez por filtro, no por cálculo

La madurez de los targets no se calcula en el código de métricas: el ETL ya filtra `semana_num = iso_week(execution_week - lag)` al cargar Flow B, garantizando que solo entren créditos con outcomes observables (decisions §8.2.4, §8.2.8). La columna `semanas_vida` fue eliminada por redundante y errónea.

---

## 2. Tablas META

### 2.1 `META_MODEL_REGISTRY`

Registro maestro de modelos y submodelos (segmentos). SCD2 por `(model_id, submodel_id, valid_from)`.

Columnas clave:

- `model_id` (str): identificador del scorecard padre. Hoy: `"BAZBOOST_V1"`.
- `submodel_id` (str): `s1`–`s11`.
- `score_min`, `score_max` (int): rango del score. Parametrizable por modelo.
- `feature_count`, `training_cutoff_date`, `owner_team`: metadata.

> El lag operativo vive en `META_VARIABLES.lag_semanas` (uno por target). `MetaModelRegistry` no tiene un campo de lag — ver ADR §8.2.24.

Reglas:

- Nunca se sobreescribe. Un re-entrenamiento del modelo crea un nuevo registro con `valid_from` nuevo y cierra el anterior.
- El Pipeline solo considera segmentos con `valid_to IS NULL`.

### 2.2 `META_VARIABLES`

Catálogo de variables por modelo. SCD2 por `(model_registry_id, variable_name, valid_from)`.

Columnas clave:

- `variable_rol`: `input` (variable del scorecard), `output` (el score total), `target` (variable a predecir).
- `variable_type`: `numeric` o `categorical`. Hoy `fisexo` es la única categórica.
- `binning_rules` (JSONText): `{"type": "fixed_cuts", "cuts": [...]}` para numéricas.
- `woe_categories` (JSONText): lista de categorías admitidas para categóricas.
- `lag_semanas` (int, sólo para targets): ventana de observación.
- `ascending_order` (bool, sólo para targets): `True` si la tasa debe crecer con el score (ej: tasa de pago); `False` si debe decrecer (ej: `b_malo*`). Se usa para detectar `ordering_violations`.
- `source_table` (str): tabla origen física (metadata informativa).

Razón para guardar bin edges en DB (decisions §8.2.6): evita hardcodear cuts en el código. Recalibración → solo cambia el registro SCD2, el ETL lee la versión activa.

**Notas operativas:**

- Los score bins (`0-100`, `100-200`, …, `900-1000`) están definidos como constante en `bootstrap.py` (`SCORE_BINS`). Cambiar a bins dinámicos (percentiles, equi-width) requiere extender `_baseline_score_distributions()`.
- `fisexo` es la única variable categórica del modelo actual, hardcodeada en `bootstrap.py`. Agregar un nuevo modelo con otras variables categóricas implica revisar ese hardcode.
- **Cómo se sabe que una variable es "intermedia" (no input del scorecard).** La fuente de verdad de los inputs oficiales del modelo es `data/inputs/raw_tables/Variables_por_segmento.xlsx` (entregado por crédito). `src/mlmonitor/data/variable_mapping.py` cruza ese Excel contra el dump SERC (`variables_serc.csv`): lo que aparece en SERC pero no en el Excel queda listado como `EXTRA_SERC_VARIABLES` y se considera intermedio (cálculo derivado o lookup que SERC reporta pero no entra al regresor). `serc_to_canonical()` devuelve `None` para esas, así que el ETL las ignora y no aparecen en `META_VARIABLES`. Si crédito declara explícitamente que alguna intermedia debe monitorearse, hay que: (1) agregarla a `CANONICAL_VARIABLES` para el segmento que aplica, y (2) re-bootstrap.

### 2.3 `META_METRIC_THRESHOLDS`

Catálogo de métricas y umbrales de alerta. SCD2 por `(model_registry_id, metric_name, valid_from)`.

Columnas clave:

- `metric_name` (str): `"psi"`, `"null_rate"`, `"gini_b_malo8_13"`, `"ks_b_malo4_6"`, `"ordering_violations_b_malo2_4"`, `"gini_edad"`, etc.
- `model_registry_id` (FK): siempre poblado — los thresholds son **per-segmento** (ver §4.5 y ADR §8.2.23). El campo es nullable en el schema por compatibilidad SCD2 pero hoy todas las filas activas tienen valor.
- `warning_threshold`, `critical_threshold` (float).
- `direction` (str): `"higher_worse"` (PSI, null_rate, ordering_violations) o `"lower_worse"` (Gini, KS). Se aplica con regla canónica en código, no se confía en el CSV.

El evaluador en `metrics/calculator.py::AlertEvaluator` busca por `(metric_name, model_registry_id)` y cae al global (`model_registry_id IS NULL`) si no existe — fallback que hoy queda inactivo (no se insertan globales) pero se preserva para futuras métricas globales explícitas. El acoplamiento `metric_id` ↔ SCD2 de thresholds es deuda conocida — ver [`../backlog.md`](../backlog.md) §4.

### 2.4 `META_BASELINE_DISTRIBUTIONS`

Distribuciones de referencia del baseline de entrenamiento. Una sola fila por `(model_registry_id, variable_id, bin_label)`.

Razón para separarla de `FACT_DISTRIBUTIONS` (decisions §8.2.18): el baseline **no es una semana de producción**. Es un artefacto de entrenamiento con ciclo de vida distinto (se reemplaza al re-entrenar, no cada lunes). Antes existía un flag `reference_flag` en `FACT_DISTRIBUTIONS` pero era semánticamente incorrecto: confundía distribuciones de producción con referencia estática y permitía filtrar mal.

Redundancia intencional: se guarda `bin_percentage = bin_count / total_records` como campo derivado. Razón: el cálculo de PSI es read-heavy; evitar el `bin_count / total_records` en cada query acelera la consulta. Ambos campos se setean juntos al insertar y **nunca se actualizan por separado**.

El baseline se construye desde `base_train_test_bb.csv` (formato WIDE, ~501K filas, 146 columnas) y no desde `variables_serc` porque tiene los nombres canónicos ya resueltos como columnas directas (decisions §8.2.16).

#### Estructura del CSV fuente (`base_train_test_bb.csv`)

- **Ubicación:** `data/inputs/raw_tables/base_train_test_bb.csv`.
- **Formato:** WIDE — una fila por crédito (`fiidscoreds`), con las variables del scorecard como columnas directas. Contrasta con `variables_serc`, que es LONG (una fila por solicitud × variable).
- **Dimensiones:** ~501,585 filas, 146 columnas.
- **Segmentación:** columna `fiidsegmento` (1–11 = segmentos monitoreados del scorecard; pueden aparecer otros segmentos, p. ej. 14, que no forman parte del monitoreo del pipeline).
- **Variables canónicas:** las de `CANONICAL_VARIABLES` en `variable_mapping.py` existen como columnas en el baseline con nombres canónicos (p. ej. `cp_mean_ti_8_13_rez`, `edad`, `fisexo`); no hace falta mapeo SERC→canónico para leerlas desde este CSV.
- **Score:** columna `fnpuntaje` (0–1000).
- **Targets en el baseline:** `b_malo2_4`, `b_malo4_6`, `b_malo8_13`, `b_malo8_16`, `b_malo14_26`. `b_malo14_52` no aparece como columna en este CSV; sí en `muestra_weekly`.
- **Otras columnas relevantes (no exhaustivo):** `fdregistro`, `fecha_solicitud`, `fecha_surt`, `vintage`, `vintage_bis`, `fcnombreproducto`, `fccolor`, `finivelriesgo`, `segmento_bb_concluyente`, `fdcsaldocapital`, `m_produc`, y decenas de variables transaccionales/buró no usadas como inputs del scorecard.

---

## 3. Tablas FACT

### 3.1 `FACT_DISTRIBUTIONS`

Distribuciones semanales de producción. Una fila por `(model_registry_id, variable_id, origination_week, bin_label)`.

- `origination_week`: semana de **scoreo** (ver §1.6).
- `bin_count`, `bin_percentage`: conteo y proporción en el bin.
- `null_count`: cuántos valores nulos hubo esa semana (usado para `null_rate`).
- `sum_value` (float): suma de valores del bin, para poder calcular medias por bin si se necesita.
- `total_records`: denominador semanal.

Origen: Flow A del ETL incremental. Solo contiene datos **de producción** (la referencia vive en `META_BASELINE_DISTRIBUTIONS`).

### 3.2 `FACT_PERFORMANCE_BINNED`

Outcomes agregados por decil de score. Una fila por `(model_registry_id, origination_week, execution_week, metric_type, score_bin)`.

- `origination_week`: semana de **surtimiento** (ver §1.6).
- `execution_week`: semana en que corrió el ETL = `origination_week + lag`.
- `metric_type`: nombre del target (`b_malo2_4`, `b_malo8_13`, etc.). Una fila por target × score_bin × semana × segmento.
- `score_bin`, `score_midpoint`: intervalo del decil (`"0-100"`, midpoint 50) y su punto medio entero.
- `count_total`, `count_event_real`: denominador y numerador. Las tasas se calculan al vuelo (`count_event_real / count_total`), no se almacenan.
- `sum_predicted_score`: para calibración, score promedio = `sum_predicted_score / count_total`.

Regla de bin superior (decisions §8.2.13): el último bin (`900-1000`) incluye `score = score_max`. Antes se excluía, creando inconsistencia con los datos individuales.

Esta tabla alimenta `check_ordering_violations` (business_metrics.py) y la `business_metrics_table` del PDF. Es fallback para Gini/KS si no hay datos individuales.

### 3.3 `FACT_PERFORMANCE_INDIVIDUAL`

Outcomes a nivel de crédito individual. Una fila por `(credito_id, model_registry_id, ventana)`.

- `credito_id`: string único del crédito (`fiidscoreds` en los CSVs).
- `origination_week`: semana de **surtimiento** (ver §1.6).
- `execution_week`: semana de observación del outcome.
- `fnpuntaje`: score continuo real del crédito.
- `ventana`: nombre del target (equivalente a `metric_type` en la tabla binned).
- `flag`: 0 o 1 (nunca null).

Razón para existir (decisions §8.2.5): Gini y KS desde datos individuales no tienen error de discretización; las curvas de Lorenz son exactas. Es la fuente primaria; `FACT_PERFORMANCE_BINNED` es fallback.

Trade-off: más filas leídas por cálculo (~28K vs 10 en SQLite). Ya documentado: para Postgres con 1M+ créditos se recomienda el índice compuesto `(model_registry_id, origination_week, ventana)` — ver [`../backlog.md`](../backlog.md) §1.

### 3.4 `FACT_METRICS_HISTORY`

Historial de métricas calculadas. Una fila por `(model_registry_id, calculation_week, metric_id, variable_id)`.

- `calculation_week`: semana del cálculo del pipeline.
- `metric_id`: FK a `META_METRIC_THRESHOLDS.id`.
- `variable_id`: FK a `META_VARIABLES.id` (nullable — nulo para métricas agregadas por segmento como `max_psi`, Gini, KS, violations).
- `metric_value` (float).
- `alert_label` (str): `"OK"` | `"WARNING"` | `"CRITICAL"`.
- `details` (JSONText): información extra (p.ej. `{"variable": "edad", "max_variable": "edad", "is_max_psi": true}` o `{"origination_week": "2025-10-13", "target": "b_malo8_13"}`).
- `calculated_from` (str): tabla origen (`"FACT_DISTRIBUTIONS"`, `"FACT_PERFORMANCE_INDIVIDUAL"`, `"FACT_PERFORMANCE_BINNED"`).

Esta es la única tabla que escribe el **Pipeline**, no el ETL. La única tabla que **no se sincroniza** en el escenario VM/Cloud legado (decisions §8.2.15) — queda obsoleto pero útil como contexto.

Conocidos (pendientes de resolver antes de BI — ver [`../backlog.md`](../backlog.md)):

- **No es BI-friendly**: requiere 3 JOINs para obtener fila legible; el target va embebido en `metric_name` sin columnas separadas; `origination_week` de Gini/KS vive dentro del JSON `details`.
- **Acoplamiento `metric_id` ↔ SCD2**: si un threshold se versiona, el nuevo `id` es tratado como una métrica distinta por el `UniqueConstraint`, permitiendo insertar duplicados conceptuales.

---

## 4. Reglas de negocio derivadas

Extraídas de [`§0`](#0-datos-raw-y-contexto-de-negocio) y del código:

### 4.1 Targets y lags (BAZBOOST_V1)

| Target | lag_semanas | Semántica |
|---|---|---|
| `b_malo2_4` | 4 | Malo a 2–4 semanas |
| `b_malo4_6` | 6 | Malo a 4–6 semanas |
| `b_malo8_13` | 13 | Malo a 8–13 semanas |
| `b_malo8_16` | 16 | Malo a 8–16 semanas |
| `b_malo14_26` | 26 | Malo a 14–26 semanas (ventana media) |
| `b_malo14_52` | 52 | Malo a 14–52 semanas (ventana larga) |

**Nota sobre dev:** la ventana del CSV `20260105_s32_s41` (semanas 32–41 de 2025, `semana_observacion=202602`) permite observar `b_malo8_13` (W2 − 13 = W41 ✓) y `b_malo8_16` (W2 − 16 = W38 ✓). El resto de targets requieren cohortes fuera de esa ventana y aparecerán cuando los CSVs cubran historia suficiente. En producción los lags 26 y 52 también pueden tardar varias semanas en empezar a poblarse hasta que el origen acumule historia.

**Convención de `lag_semanas` para targets `b_malo<a>_<b>`:** se usa el **extremo superior** de la ventana (`b`) en todos los casos. Significa: número de semanas que un crédito necesita haber existido para observar el outcome completo de la ventana — es la maduración mínima requerida. (Corregido 2026-04-28: `b_malo8_13` cargaba `lag=8` por error; ver corrigendum en `docs/decisions.md` §8.2.22.)

### 4.2 Filtros del ETL

Solo se procesan créditos con `flg_baz_boost = 1 AND flg_surtida = 1`. El primero selecciona créditos scoreados por el modelo; el segundo, créditos efectivamente desembolsados.

### 4.3 Lógica temporal

Para un target con lag `L` y semana de ejecución `W`:

- Flow A filtra `_reference_week == W` (semana de scoreo).
- Flow B filtra `semana_num = iso_week(W - L)` (cohorte madura).
- `semana_observacion` es el techo: si `W > semana_observacion`, los outcomes podrían no estar completos; el ETL auto-detecta `W = MAX(semana_observacion)` por defecto.

### 4.4 Dirección de ordenamiento esperada

- Tasas de **malo** (todas las variantes `b_malo*`): deben **decrecer** conforme sube el score (bajo score = alto riesgo). `ascending_order = False`.
- Tasas de **pago** (si aplica): deben **crecer** con el score. `ascending_order = True`.

La tolerancia numérica de `check_ordering_violations` es `0.005` (medio punto porcentual).

### 4.5 Umbrales por segmento

**Fuente:** `data/inputs/raw_tables/tresholds_monitoreo.csv` (entregado por crédito), parseado por `data/threshold_loader.py` y persistido por `bootstrap.py::_populate_meta_metric_thresholds()`. Decisión documentada en ADR §8.2.23.

**Métricas por segmento** (~28 filas/segmento × 11 = ~315 filas activas):
- `psi`, `null_rate` (2)
- `gini_<target>`, `ks_<target>`, `ordering_violations_<target>` para los 6 targets de §0.2 (18)
- `gini_<scorecard_var>` para cada variable canónica del segmento (8 a 15 según `len(CANONICAL_VARIABLES[seg])`)

**Direction canónica (en código, no del CSV):**
- `higher_worse`: `psi`, `null_rate`, `ordering_violations*`
- `lower_worse`: `gini*`, `ks*`

**Defaults** (cuando el CSV no trae la métrica esperada para el segmento):

| Métrica                       | warning | critical |
|-------------------------------|---------|----------|
| `psi`                         | 0.10    | 0.20     |
| `null_rate`                   | 0.03    | 0.10     |
| `gini_<target>`               | 0.35    | 0.25     |
| `ks_<target>`                 | 0.20    | 0.15     |
| `ordering_violations_<target>`| 1       | 2        |
| `gini_<scorecard_var>`        | 0.15    | 0.05     |

**Filtros del CSV:** se ignoran filas con `EXTRA_SERC_VARIABLES` (intermedias del scorecard), `gini_INTERCEPTO`, y métricas/targets fuera del catálogo. Variables en formato SERC se mapean a canónico vía `serc_to_canonical` (p. ej. `gini_EDAD` → `gini_edad`).

### 4.6 Segmentación (BAZBOOST_V1)

11 submodelos `s1..s11` agrupados en 5 grupos (G1–G5). Nombres de grupos y conteos de features por segmento viven en `src/mlmonitor/data/variable_mapping.py` (`SEGMENT_GROUP_NAMES`, `SEGMENT_FEATURE_COUNTS`).

---

## 5. Cambios en el schema — flujo operativo

1. **Proponer** el cambio en texto (nueva columna / tabla / constraint) y esperar autorización del usuario (regla dura de `CLAUDE.md §3`).
2. **Editar** `src/mlmonitor/db/models.py` + agregar entrada en `docs/decisions.md` como nueva subsección `§8.2.x` con contexto, decisión, consecuencias.
3. **Actualizar** fixtures de `tests/conftest.py` si aplica.
4. **Reset de la DB local**: `rm mlmonitor_dev.db && poetry run python scripts/run_bootstrap.py`.
5. **Re-ejecutar** el notebook `validacion_metricas_baseline.ipynb` si el cambio afecta distribuciones o performance.
6. En RDS: migración manual (no hay Alembic configurado todavía).

Consulta [`../decisions.md`](../decisions.md) para la historia completa de cambios al schema.
