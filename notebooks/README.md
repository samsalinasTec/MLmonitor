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

### Nota sobre `first_payment_default2`

El baseline (`base_train_test_bb.csv`) **no incluye** la columna `first_payment_default2`. El pipeline sí calcula Gini/KS para ese target, por lo que la sección 1D lo deja en NaN y la comparación (§2) queda vacía para el par segmento × target correspondiente. No es bug: es diferencia de cobertura entre los dos artefactos.

### Cuándo re-ejecutarlo

- Tras cualquier cambio que toque `metrics/psi.py`, `metrics/performance.py`, `data/bootstrap.py` o `data/incremental_etl.py`.
- Tras modificaciones al schema (`db/models.py`) que afecten `FACT_METRICS_HISTORY`.
- Como verificación antes de mergear un refactor de cálculo.

---

## `eda_muestra_weekly_s32_s41.ipynb`

EDA ad-hoc sobre `muestra_weekly` (semanas 32–41 del dataset dummy). No forma parte del ciclo de validación — documenta hallazgos puntuales de exploración.

## `model_data_explorer.ipynb`

Explorador genérico del modelo y sus variables. Útil para entender la estructura antes de modificar `variable_mapping.py` o agregar un nuevo modelo.
