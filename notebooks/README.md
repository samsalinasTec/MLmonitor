# Notebooks — MLMonitor

Notebooks de exploración y validación. No son parte del pipeline productivo; sirven como *ground truth* manual y herramienta de EDA.

---

## `validacion_metricas_baseline.ipynb`

Notebook autocontenido que calcula **PSI, Gini y KS** directamente desde los CSVs raw (`base_train_test_bb.csv`, `variables_serc_*.csv`, `muestra_weekly_*.csv`) **sin importar `mlmonitor`**, y compara los resultados contra los almacenados en `FACT_METRICS_HISTORY`.

**Propósito:** detectar regresiones silenciosas del pipeline comparándolo con un cómputo manual independiente. La motivación está documentada en `../docs/decisions.md §8.2.17`.

### Estructura

- **1A — PSI vs baseline de entrenamiento:** Referencia = distribución desde `base_train_test_bb.csv` (WIDE); actual = semana elegida de `variables_serc` (LONG). Bin edges numéricos por quantiles del baseline (10 bins); `fisexo` como categórica. Incluye PSI del score con bins fijos 0–1000.
- **1B — PSI vs primera semana SERC:** Replica la lógica histórica del producto (referencia = primera semana con cobertura de los 11 segmentos en `variables_serc`). Útil para comparar contra el pipeline actual, que desde el refactor §8.2.18 usa baseline en vez de primera semana — esta sección quedó como legado de validación.
- **1C — Gini/KS desde `muestra_weekly`:** Cohortes por `semana_num` / lag alineadas al ETL; score invertido con `SCORE_MAX - fnpuntaje`.
- **1D — Gini/KS desde baseline:** Informativo (poder discriminativo en entrenamiento); no alimenta el pipeline.
- **2 — Comparación con BD:** Lee SQLite (`mlmonitor_dev.db`) y compara PSI (1B vs pipeline) y Gini/KS (1C vs pipeline).

### Resultados de validación (ejecución documentada en la sección 2)

| Métrica | Coincidencias | Diferencia |
|---|---|---|
| PSI (1B vs pipeline) | 71/71 | diff < 0.001 |
| Gini (1C vs pipeline) | 33/33 | diff = 0 |
| KS (1C vs pipeline) | 33/33 | diff = 0 |
| Gini cruzado con `sklearn.metrics.roc_auc_score` | — | máx ~0.0035, media ~0.0007 |

### Observaciones de la sección 1A

- Comparar 1A vs 1B muestra diferencias grandes de PSI (media de `|diff|` ~2.64, máx ~15.52): la primera semana de producción no aproxima la distribución del baseline; `n_ref` en la semana mínima puede ser muy bajo frente al volumen del entrenamiento.
- PSI altos de score frente al baseline en varios segmentos son **esperables** al contrastar un histórico amplio con un snapshot semanal — justifican el refactor §8.2.18.

### Cuándo re-ejecutarlo

- Tras cualquier cambio que toque `metrics/psi.py`, `metrics/performance.py`, `data/bootstrap.py` o `data/incremental_etl.py`.
- Tras modificaciones al schema (`db/models.py`) que afecten `FACT_METRICS_HISTORY`.
- Como verificación antes de mergear un refactor de cálculo.

---

## `exploracion_thresholds_2026_04_27.ipynb`

Diagnóstico del CSV `data/inputs/raw_tables/tresholds_monitoreo.csv` entregado por el equipo de crédito (vs. configuración actual de `META_METRIC_THRESHOLDS`). Notebook **read-only**: no escribe en la DB.

**Propósito:** detectar mismatches antes de construir el loader que migrará a thresholds per-segmento. Sirve como insumo para resolver dudas con crédito (D1–D5 al final del notebook).

### Estructura

1. Setup
2. Carga CSV (limpieza de filas vacías y trailing)
3. Snapshot de DB (META_VARIABLES + META_METRIC_THRESHOLDS activos)
4. Mapeo SERC→canónico y clasificación por tipo (`basic`, `target`, `scorecard_var`, `intermediate`, `intercepto`, `unknown`)
5. Diff por segmento — variables de scorecard (CSV vs `CANONICAL_VARIABLES`)
6. Diff por segmento — targets monitoreados
7. Diff de direcciones (CSV vs regla canónica de la ADR §8.2.22)
8. Sanity checks (filas vacías, duplicados, consistencia warning/critical, rangos plausibles)
9. Resumen ejecutivo (tabla con conteos)
10. Dudas pendientes para crédito (D1–D5)

### Hallazgos preliminares

- 165 thresholds de target, 117 de scorecard, 42 de variables intermedias (EXTRA_SERC), 22 básicas (psi/null_rate), 10 de INTERCEPTO.
- **121 mismatches de `direction`** vs la regla canónica (segmentos `bb_2..bb_11` traen el campo invertido en varias filas).
- **`b_malo8_16` falta en los 11 segmentos** del CSV.
- Varios segmentos traen variables de scorecard que no están en `Variables_por_segmento.xlsx` (probable arrastre de versiones anteriores del modelo).
- 0 duplicados, 0 inconsistencias `warning` vs `critical` cuando se aplica la regla canónica.

### Cuándo re-ejecutarlo

- Cada vez que el equipo de crédito entregue un CSV nuevo de thresholds.
- Tras cambios a `data/variable_mapping.py` (`CANONICAL_VARIABLES`, `EXTRA_SERC_VARIABLES`).
- Antes de implementar el loader de thresholds, para confirmar que las dudas siguen vigentes.

---

## `eda_muestra_weekly_s32_s41.ipynb`

EDA ad-hoc sobre `muestra_weekly` (semanas 32–41 del dataset dummy). No forma parte del ciclo de validación — documenta hallazgos puntuales de exploración.

## `model_data_explorer.ipynb`

Explorador genérico del modelo y sus variables. Útil para entender la estructura antes de modificar `variable_mapping.py` o agregar un nuevo modelo.
