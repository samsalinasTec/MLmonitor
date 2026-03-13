# Datos Faltantes — Área de Crédito

Documento generado a partir del análisis de las tablas raw entregadas
(`variables_serc.csv`, `muestra_weekly.csv`, `Variables_por_segmento.xlsx`,
`layout_variables_bb.csv`) y su mapeo al modelo estrella de MLMonitor.

---

## 1. Datos Críticos (bloquean funcionalidad core)

### 1.1 Distribuciones baseline de entrenamiento

| Necesidad | Tabla destino | Columna |
|---|---|---|
| Distribuciones de cada variable al momento del entrenamiento del modelo (referencia para calcular PSI) | `FACT_DISTRIBUTIONS` | `reference_flag = 1` |

**Detalle**: Se necesitan las distribuciones históricas de cada variable del scorecard
en la ventana de entrenamiento. Actualmente todos los registros en `variables_serc.csv`
son de producción (Nov 2024 – Ene 2026). Sin baseline, el PSI se calcula contra la
primera semana disponible, lo cual no es la referencia correcta.

**Formato sugerido**: Mismo formato que `variables_serc.csv` pero con registros del
periodo de entrenamiento, o bien un archivo con las distribuciones pre-calculadas por
variable/segmento (bins, conteos, porcentajes).

### 1.2 Data de vintage maduro con labels reales

| Necesidad | Tabla destino | Columna |
|---|---|---|
| Registros con labels de mora/default maduros (8+ semanas) | `FACT_PERFORMANCE_OUTCOMES` | `count_event_real` |

**Detalle**: En `muestra_weekly.csv` las columnas `b_malo14_26`, `b_malo14_52`,
`b_malo2_4`, `b_malo4_6`, `b_malo8_13`, `b_malo8_16` son **todas 0** en los 1,000
registros. Solo `first_payment_default2` tiene datos parciales (37 defaults / 370
registros no nulos). Esto indica que los vintages (Dic 2025 – Ene 2026) no han
madurado lo suficiente.

**Se necesita**: Muestras de vintages más antiguos (e.g., Ene–Jun 2025) donde los
labels `b_malo*` ya estén poblados para poder calcular Gini, KS, y tasas de
roll-forward.

### 1.3 Muestras históricas semanales

| Necesidad | Tabla destino | Columna |
|---|---|---|
| Más semanas de datos (actualmente solo semanas 202601 y 202602) | Todas las tablas FACT | `reference_week`, `date_score_key` |

**Detalle**: Para análisis de tendencias y detección de drift se necesitan al menos
12-20 semanas de historia. Actualmente `muestra_weekly.csv` solo cubre 2 semanas
(202601, 202602). `variables_serc.csv` tiene más dispersión temporal pero concentrada
en las últimas semanas.

---

## 2. Datos Importantes (enriquecen metadata)

### 2.1 Descripciones individuales de cada segmento

| Necesidad | Tabla destino | Columna |
|---|---|---|
| Descripción detallada de cada uno de los 11 segmentos | `META_MODEL_REGISTRY` | `model_description` |

**Detalle**: `fcnombresegmento21` en SERC solo tiene 3 grupos (`NO FILES`,
`THIN FILES`, `BIG FILES`), no descripciones únicas por segmento. El dummy usaba
descripciones como "No file - sin historial crediticio", "In file < 6 meses", etc.
Se necesitan las descripciones reales para cada segmento 1-11.

### 2.2 Lag de semanas por segmento

| Necesidad | Tabla destino | Columna |
|---|---|---|
| Ventana de observación de outcomes (semanas) por segmento | `META_MODEL_REGISTRY` | `lag_semanas` |

**Pregunta**: Es 8 semanas para todos los segmentos, o varía por segmento?

### 2.3 Fecha de corte de entrenamiento

| Necesidad | Tabla destino | Columna |
|---|---|---|
| Fecha hasta la que se usó data para entrenar cada segmento | `META_MODEL_REGISTRY` | `training_cutoff_date` |

### 2.4 Definición del target

| Necesidad | Tabla destino | Columna |
|---|---|---|
| Qué predice el modelo en lenguaje natural por segmento | `META_MODEL_REGISTRY` | `target_definition` |

**Ejemplo**: "Probabilidad de incumplimiento en ventana de 8 semanas" o similar.

### 2.5 Reglas de binning / categorías WoE

| Necesidad | Tabla destino | Columna |
|---|---|---|
| Definición de bins para cada variable numérica del scorecard | `META_VARIABLES` | `binning_rules` |
| Categorías WoE para variables categóricas | `META_VARIABLES` | `woe_categories` |

**Detalle**: Actualmente se usan quantiles automáticos. Las reglas de binning reales
del scorecard producirían distribuciones más significativas.

### 2.6 Tabla fuente por variable

| Necesidad | Tabla destino | Columna |
|---|---|---|
| Nombre de la tabla/sistema de donde proviene cada variable | `META_VARIABLES` | `source_table` |

---

## 3. Aclaraciones Pendientes

### 3.1 Join key entre layout_variables_bb y muestra_weekly

`layout_variables_bb.csv` usa `cliente_unico` y `muestra_weekly.csv` usa `cte_unico`.
Se verificó que **no hay overlap** entre ambos campos (0 coincidencias de 1,000 vs 975
registros). Se necesita saber cómo se relacionan estas tablas y si `layout_variables_bb`
corresponde a los mismos clientes.

### 3.2 Variables extra en SERC no presentes en Variables_por_segmento

En `variables_serc.csv` hay **29 variables** que no están en la lista canónica de
`Variables_por_segmento.xlsx`. Son variables intermedias o de soporte que se usan
durante el scoring. Se necesita confirmar si estas variables también deben monitorearse
o si solo se monitorean las variables oficiales del scorecard.

**Lista completa de variables extra en SERC:**

| Variable SERC | Segmentos donde aparece |
|---|---|
| `ANTDIG` | 5 |
| `AVGNUMGUARDADITO6` | 6 |
| `AVGNUMSEMANASACT` | 5, 6 |
| `AVGSLDGUARDADITO6` | 10 |
| `COCTOTCONSCDC3M12M` | 3, 4 |
| `CPTI813REZ` | 1 |
| `DIASULTDISP` | 4 |
| `DISPMENSUAL` | 4 |
| `EDOMEDIANCONTEOSREZ` | 5, 9 |
| `ICVPC` | 2 |
| `INDSINCUENTA` | 6 |
| `MAXSEMULTPAGAACTIVAS` | 3 |
| `MONTOCOMPRADOLARES` | 6 |
| `MONTOPAGO` | 8 |
| `MTODEPINTERES12M` | 7 |
| `NUFAM2` | 4 |
| `NUMDEPNOMINAN3` | 4 |
| `NUMNOMONETARIAS` | 4, 6 |
| `NUMPRESTAMOSPERS` | 5, 7 |
| `NUMTIENDADESC` | 4 |
| `OOTCONSCDC12M` | 10 |
| `OOTCONSCDC3M` | 7 |
| `PCTTOTVIDA13S` | 2, 7 |
| `PORCFCONSTCDC12M` | 2, 8 |
| `PORCFCONSTCDC3M` | 8 |
| `PROMTXNDEPINTERES12M` | 4, 10 |
| `SLDFINMESN3` | 8 |
| `SLDFINMESN6` | 4, 7 |
| `SLDPROMMENSUAL` | 6 |
| `SLDPROMMES` | 8 |
| `TXNCOMPRA` | 9 |

### 3.3 Mapeo de nombres SEXO vs fisexo

En SERC la variable de sexo aparece como `SEXO`, en `Variables_por_segmento.xlsx`
como `fisexo`. Se asume que son la misma variable. Confirmar.

### 3.4 PORCFCONSTCDC12M vs porc_f_cons_cdc_12m

En SERC: `PORCFCONSTCDC12M` (con "T" extra). En Variables_por_segmento:
`porc_f_cons_cdc_12m`. Probablemente la misma variable. Confirmar.

### 3.5 Sentinel value -100

En `variables_serc.csv`, `fcvalor_variable` usa `-100` como valor para ciertos
registros (e.g., INTERCEPTO siempre es -100, y algunas variables tienen -100 cuando
el valor real no existe). Confirmar que -100 es el sentinel estándar para missing
y no un valor válido.

### 3.6 date_outcome_key para performance

Para calcular métricas de performance (Gini, KS, roll-forward), se necesita saber
si el lag es uniforme (8 semanas) para todos los segmentos, y cómo se calcula
`date_outcome_key` a partir de la fecha del score.

---

## 4. Resumen de Estado Actual del ETL

| Tabla | Estado | Rows cargadas | Notas |
|---|---|---|---|
| `META_MODEL_REGISTRY` | Completa | 11 | 11 segmentos de BazBoost |
| `META_VARIABLES` | Completa | 106 | 95 input + 11 score output |
| `META_METRIC_THRESHOLDS` | Completa | 6 | Umbrales globales |
| `FACT_DISTRIBUTIONS` | Parcial | ~10,787 | Variables + scores; falta baseline (reference_flag=1) |
| `FACT_PERFORMANCE_OUTCOMES` | Mínima | ~35 | Solo first_payment_default con 144 registros BazBoost; b_malo* todo en 0 |
| `FACT_METRICS_HISTORY` | Downstream | 0 | Se calcula a partir de las tablas FACT anteriores |
