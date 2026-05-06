# devlog.md — Bitácora del agente

Bitácora viva de las sesiones de trabajo del agente sobre MLMonitor. No es ADR: aquí van "qué hice / qué probé / qué sigue" en formato corto. Para decisiones arquitectónicas formales ver [`docs/decisions.md`](docs/decisions.md).

Formato: encabezado por fecha ISO (`## YYYY-MM-DD`) + bullets cortos. Entradas más recientes **arriba**.

---

## 2026-05-06

- **Bootstrap V2: baseline desde `variables_serc` en vez de `base_train_test_bb.csv`** (`feature/new_psi_calculation`). Reemplazo del baseline de entrenamiento (formato WIDE, ~501K filas) por las **primeras 4 semanas ISO del año en curso** dentro de `variables_serc_*.csv` (LONG). Motivación: el baseline original es la población histórica de entrenamiento; los segmentos productivos ya divergieron de esa distribución, lo que inflaba PSI sin reflejar drift reciente. Usar Q1 del año actual ancla el baseline a la misma fuente y a una temporalidad cercana a la operación.
  - **Nuevos archivos:** `src/mlmonitor/data/bootstrap_v2.py` (subclase `ModelBootstrapV2(ModelBootstrap)` que sobreescribe sólo `_populate_baseline_distributions`) y `scripts/run_bootstrap_v2.py` (runner paralelo a `run_bootstrap.py` con flags `--year`, `--n-weeks`, `--variables-serc-file`, `--db-url`). META_MODEL_REGISTRY, META_VARIABLES y META_METRIC_THRESHOLDS quedan idénticas (heredadas).
  - **Lógica del nuevo baseline:** lee `variables_serc_*.csv` con `usecols` reducido (6 cols), deriva `_iso_year`/`_iso_week` desde `fdregistro_solicitud` (epoch ms → date), filtra a `(year=2026, weeks∈{1..4})`, mapea SERC→canónico vía `serc_to_canonical`, y calcula bins exactamente como `bootstrap.py`: numéricas vía `pd.qcut(q=10)` con persistencia de cuts en `MetaVariables.binning_rules`, categóricas (fisexo) por `value_counts` con persistencia en `woe_categories`, score con `SCORE_BIN_CUTS` fijos. Ventana W1-W4 2026 = lunes ISO `2025-12-29`, `2026-01-05`, `2026-01-12`, `2026-01-19`.
  - **Validación end-to-end (SQLite local):** `rm mlmonitor_dev.db && poetry run python scripts/run_bootstrap_v2.py --db-url sqlite:///mlmonitor_dev.db` → 11 segmentos × 139 variables × 216 thresholds × **808 baseline rows**. Ventana baseline: 2.83M filas SERC, **235 364 créditos únicos**.
  - **Resultado del experimento (`run_pipeline.py --date 2026-04-06`):** estado de flota pasa de `1 OK | 3 WARNING | 7 CRITICAL` (baseline original) a `1 OK | 6 WARNING | 4 CRITICAL` (baseline V2). 3 segmentos pasaron CRITICAL → WARNING. Consistente con la hipótesis: con baseline reciente, las distribuciones recientes están más cerca de él → PSI menor.
  - **Confirmación del usuario:** el cambio se queda. Bootstrap V2 será el camino oficial. Próximo paso: deprecar `bootstrap.py` original o consolidar en uno solo (pendiente decisión sobre si mantener path para `base_train_test_bb.csv` con flag).
  - **ADR formal:** `docs/decisions.md §8.2.29` documenta motivación, supersede de §8.2.16 y §8.2.18 (sólo en cuanto a la fuente del baseline; el schema y el refactor de tabla separada `META_BASELINE_DISTRIBUTIONS` se mantienen).
  - **Pitfall observado en sesión:** la env var `DB_URL=` no es suficiente para forzar SQLite local porque `config/settings.py::_build_settings` sobreescribe `db_url` con el valor de Secrets Manager si AWS está disponible. Forma correcta para experimentos locales: pasar `--db-url sqlite:///mlmonitor_dev.db` explícito en cada script.

---

## 2026-05-05

- **Ajuste de umbrales de agregación de severidad.** Los umbrales en `config/settings.py` que controlan cuándo un segmento sube de OK a WARNING o CRITICAL eran demasiado sensibles. Valores anteriores → nuevos:
  - `status_crit_count_to_critical`: 3 → **5** (antes bastaban 3 métricas agregables críticas para CRITICAL)
  - `status_crit_count_to_warning`: 1 → **3** (antes bastaba 1 crítica agregable para WARNING)
  - `status_warn_count_to_warning`: 4 → **8** (antes bastaban 4 warnings agregables para WARNING)
  - La regla de headline CRITICAL (PSI score, Gini/KS del target primario) sigue igual: 1 headline crítica → CRITICAL inmediato.
- **Re-ejecución del pipeline** con `--date 2026-04-06`: resultado de flota pasó a **5 OK | 5 WARNING | 1 CRITICAL** (antes 0 OK | 0 WARNING | 11 CRITICAL con la regla vieja). No fue necesario borrar registros ni re-correr ETL — `overall_status` se computa al vuelo en `_aggregate_status()`, no se persiste en BD.
- **Documentación:** nueva sección `data_model.md §4.7` con el árbol de decisión completo, tabla de umbrales, y explicación de headline vs agregables. Docstring de `_aggregate_status` actualizado con referencia cruzada a la doc.

---

## 2026-05-04

- **Resumen ejecutivo en card** (`feature/new_psi_calculation`): rediseño visual del `narrative-box` que envuelve la narrativa del LLM. Antes era una caja gris con border-left rojo y un `<h2>Análisis Ejecutivo</h2>` externo. Ahora es una card con fondo `#fff7f6` (rojo muy sutil), borde general `#f4cdc9`, top-border de 3px en rojo Elektra `#DC1F0F`, padding mayor y radius 4px. El título pasa al interior con clase `.narrative-title` (uppercase, color rojo oscuro `#A8160A`, separador inferior). Aplicado en dos lugares: `Resumen Ejecutivo` en `fleet_report.html` y `Análisis del Segmento` en cada `submodel_section.html`. Sin cambios funcionales — solo estética.

- **Bug fix paleta:** los headers de las tablas (`<thead th>`) salían invisibles (texto blanco sobre bg blanco). Causa: weasyprint 60.2 no resuelve confiablemente `var()` dentro de `background:` shorthand. Solución: hardcodear los hex (`#A8160A`, `#DC1F0F`, etc.) en lugar de `var(--elektra-*)`. Las variables CSS quedan documentadas como comentario al inicio de `styles.css` para futura referencia. Verificado: headers ahora visibles en blanco sobre rojo Elektra oscuro.

- **Rebrand Elektra + orden estricto de segmentos + bypass de demo** (`feature/new_psi_calculation`):
  - **Paleta Elektra en `templates/styles.css`**: variables CSS en `:root` (`--elektra-red: #DC1F0F`, `--elektra-red-dark: #A8160A`, `--elektra-text: #1a1a2e`, etc.). Cover-page reemplaza el gradient azul oscuro por uno diagonal blanco→rojo (110deg), texto del cover en negro/gris (sobre fondo blanco). Accents h1/h2 border y narrative-box-border pasan de púrpura/cyan/azul a rojo Elektra. Table header bg de `#1a1a3e` a `var(--elektra-red-dark)`. Status badges (OK/WARN/CRIT verde/ámbar/rojo) y cards se mantienen — son semánticos, no de marca.
  - **Logo en portada**: nueva ruta `artifacts/images/elektra-logo.png` (carpeta nueva, ya en `.gitignore` por el patrón `artifacts/`). `renderer.py` resuelve el path con `as_uri()` y lo pasa a Jinja como `logo_path`. Si el archivo no existe, el template usa fallback `<div class="cover-logo">elektra</div>` con tipografía bold en rojo.
  - **Orden estricto numérico de segmentos** (`report/builder.py`): nuevo helper `_segment_sort_key("s10") → 10` para sort numérico (no alfabético). Reemplaza el sort por `STATUS_ORDER` (eliminado, no se usaba en otro lado). Ahora el PDF presenta s1, s2, …, s10, s11 en ese orden estricto en (a) tabla "Estado por Segmento", (b) secciones individuales por segmento, (c) ranking final. Trade-off consciente: ya no hay "más urgentes primero".
  - **Tests:** 103/103 verdes (100 previos + 3 en `tests/test_segment_ordering.py` para `_segment_sort_key`).
  - **Bypass de demo en `/tmp/run_report_demo_calm.py`** (NO commiteado, replica patrón de `/tmp/run_report_alt_target.py`):
    - **Disable headline para `psi_score`**: monkey-patch sobre `builder._is_headline_alert`. `psi_score` deja de auto-escalar el segmento a CRÍTICO; ahora se cuenta como métrica agregable normal.
    - **Cap "PSI > 1 → naranja"**: copia los templates a `/tmp/templates_demo/` y modifica los condicionales Jinja en celdas y badge de PSI máximo (`fleet_report.html:99`, `submodel_section.html:73, 77`) añadiendo `or seg.psi_max > 1.0` antes del branch `'critical'`. Luego inyecta el `FileSystemLoader` apuntando a `/tmp/templates_demo` en `PDFRenderer.__init__`. PSI ≤ 1 mantiene la lógica normal de thresholds.
    - **Resultado:** fleet summary pasó de `0 OK | 0 WARNING | 11 CRITICAL` (oficial) a `0 OK | 2 WARNING | 9 CRITICAL` (demo). Los 9 críticos restantes tienen métricas críticas más allá de PSI (gini/ks fuera de threshold). PDF de demo en `artifacts/reports/mlmonitor_2026-04-06_demo.pdf`.
  - **Verificación:** `poetry run pytest` → 103/103 OK; pipeline oficial corre y genera PDF con paleta Elektra y orden numérico; bypass /tmp genera el PDF demo con números PSI grandes (16.017, 17.610) en naranja.

- **Deciles con ventana rodante + persistencia + fix PDF + portada** (`feature/new_psi_calculation`):
  - **Ventana rodante 4 semanas hacia atrás** en `metrics/decile_metrics.py`: nuevo `DECILE_WINDOW_WEEKS=4` y helper `_window_weeks(cohort_end)` análogo a `psi.py::_window_weeks`. El filtro pasa de `origination_week == cohort` a `origination_week.in_(window)` en ambas ramas (consolidada y per-target). Rationale: hacia atrás = créditos más viejos = madurez ≥ lag, sus outcomes son confiables. Hacia adelante NO sirve (créditos no maduros).
  - **Nueva tabla `FACT_DECILES_HISTORY`** (`db/models.py`): persiste deciles per-target/per-segment con `cohort_window_start`/`end`, `n_obs`, `n_events`, `event_rate`, `pct_population`. Unique constraint `(model_registry_id, calculation_week, target_variable, decile)` + helper `persist_deciles_history()` con delete-then-insert para idempotencia. Se llama desde `report/builder.py::_build_decile_charts` después de computar deciles.
  - **Fix overflow tabla "Estado por Segmento"**: nueva clase `.fleet-status-table` con `table-layout: fixed`, anchos en `<colgroup>` (7/19/11/28/10/10/7%), `font-size: 8.5pt`, `padding 0.1cm`. La columna "PSI Max (variable)" lleva nombre técnico largo (ej. `edo_median_ti_4_6_rez`) que sin layout fijo desbordaba el margen derecho.
  - **Portada PDF**: "Reporte de Monitoreo de Flota de Scorecards" → "Reporte de Monitoreo de Segmentos". También `<title>` HTML y label "Sub-scorecards monitoreados" → "Segmentos monitoreados".
  - **Tests:** 100/100 verdes (12 en `test_decile_metrics.py`, 5 nuevos: `_window_weeks` × 3, agregación 4 semanas + exclusión de futuro × 1, idempotencia de persistencia × 1).
  - **Verificación end-to-end**: `run_incremental_etl --date 2026-04-06` + `run_pipeline --date 2026-04-06 --no-email --no-llm` → PDF generado OK; query a `FACT_DECILES_HISTORY` muestra 3 targets × 11 segmentos × 10 deciles = 110 filas/target con ventanas de 4 semanas correctamente alineadas (`b_malo4_6` lag=6 → window 2026-02-02..2026-02-23).
  - **Hallazgo abierto sobre PSI:** `variables_serc_*.csv` no tiene columna `flg_surtida`. Flow A NO filtra por surtidos (físicamente no puede). Pendiente verificar con upstream si el query SQL ya filtra a surtidos o si trae toda la población originada. Si filtra → pedir versión sin filtro para que PSI considere toda la población originada (incluyendo rechazados/no surtidos).

- **PDF — i18n + heatmap + lógica de severidad** (`feature/new_psi_calculation`):
  - **Badges en español:** `OK / ADVERTENCIA / CRÍTICO`. Etiquetas internas siguen en inglés (`alert_label` en DB, clases CSS); traducción solo en el render vía `STATUS_DISPLAY_ES` expuesto como global de Jinja en `report/renderer.py`. Templates `submodel_section.html` y `fleet_report.html` ahora usan `status_es[...]`. Encabezados "Warn"/"Crit" → "Adv."/"Crít.".
  - **Heatmap "Métricas de negocio por bin":** color rojo (`rgba(239, 68, 68, α)`) → slate neutral (`rgba(71, 85, 105, α)`) en `metrics/business_metrics.py:124`. Razón: el rojo se leía como "malo" cuando solo comunica concentración.
  - **Severidad escalada:** se reemplazó la regla "1 crítica en cualquier métrica → CRÍTICO" por dos categorías:
    - *Headline* (`psi_score`, `gini_<primary>`, `ks_<primary>`): 1 crítica → CRÍTICO inmediato.
    - *Agregables* (resto): 1 crítica → ADV, ≥3 críticas → CRÍTICO, ≥4 advertencias → ADV. Configurables en `config/settings.py` (`status_*_count_to_*`). `psi_max` excluida del conteo para no doble-contar. *(Umbrales actualizados 2026-05-05 a 3/5/8 — ver entrada de esa fecha.)*
  - **`status_reason`:** nuevo campo en `SegmentMetrics`, render como nota corta junto al badge en metadata del segmento.
  - **Tests:** 95/95 verdes. Nuevo `tests/test_status_aggregation.py` con 14 casos (headline, conteo, doble-conteo, casos de borde).
  - **Verificación:** `poetry run python scripts/run_pipeline.py --date 2026-04-06 --no-email --no-llm` → PDF generado OK; HTML inspeccionado: aparecen `ADVERTENCIA`/`CRÍTICO`, ya no `WARNING`/`CRITICAL` crudos. business_table vacío esa semana (sin outcomes maduros), color slate validado por `test_business_metrics.py`.

- **PSI y null_rate con ventana rodante de 4 semanas** (`src/mlmonitor/metrics/psi.py`). Antes, ambos comparaban una sola semana de `FACT_DISTRIBUTIONS` contra el baseline; ahora agregan `current_week + las 3 anteriores` por suma de `bin_count` (no promedio de porcentajes) y renormalizan. `null_rate = Σ null_count / Σ total_records` sobre la ventana, con `total_records` deduplicado por `(variable_id, origination_week)` antes de sumar entre semanas.
- **Cobertura parcial** sin warning: si la ventana cubre <4 semanas (inicio del histórico, huecos), se usa lo que existe. Caso límite con 1 semana = comportamiento equivalente al cálculo single-week previo, lo que mantiene los tests de integración existentes pasando sin modificarlos.
- **Firma pública preservada:** `get_psi_for_variable`, `get_psi_for_all_variables`, `get_null_rates` siguen funcionando con sus argumentos previos; nuevo kwarg opcional `window_weeks=PSI_WINDOW_WEEKS=4`. `MetricsCalculator` no requiere cambios.
- **`compute_psi_from_df` intacta** — la función pura no cambia; toda la lógica nueva vive en el helper privado `_aggregate_distributions_over_window`.
- **Tests:** 81/81 (75 previos + 6 nuevos en `tests/test_psi.py::TestRollingWindow`):
  - helper de fechas (lunes ISO descendentes)
  - cobertura parcial = single-week previo
  - serie totalmente estable → PSI bajo
  - spike en current_week con historia estable → PSI atenuado
  - agregación suma `bin_count` (verificado con totales distintos: 4000 vs 1000)
  - null_rate rodante = 0.05 vs 0.20 single-week
- **ADR formal:** `docs/decisions.md §8.2.27` documenta motivación (varianza semana-a-semana), por qué suma de conteos > promedio de porcentajes, por qué 4 semanas, y alternativas descartadas (EMA, media de PSIs).
- **Re-ingesta y recálculo de la semana 15-2026 (lunes 2026-04-06):**
  - DELETE de `FACT_DISTRIBUTIONS`, `FACT_PERFORMANCE_BINNED`, `FACT_PERFORMANCE_INDIVIDUAL`, `FACT_METRICS_HISTORY` para esa semana (ETL ya había corrido).
  - `poetry run python scripts/run_incremental_etl.py --date 2026-04-06`
  - `poetry run python scripts/run_pipeline.py --date 2026-04-06 --no-email --no-llm`
- Qué sigue:
  - Validar que el cambio de PSI en producción reduce alertas ruidosas. Comparar 2-3 semanas históricas con la nueva métrica vs la vieja en notebook.
  - Decidir si el ADR D6 (refresco del baseline) cambia con esta métrica: si el baseline se refresca con frecuencia, la ventana rodante interactúa con esa cadencia.

---

## 2026-04-30

- **Gráficas de deciles reales en el PDF.** La sección "Métricas de Negocio por Decil" en `submodel_section.html` realmente mostraba bines fijos de ancho 100 (no percentiles). Se renombró a `Métricas de negocio por bin de score (ancho fijo)` y se agregó debajo una nueva sección `Métricas de negocio por decil` con dos gráficas matplotlib por segmento:
  - **Consolidada:** cohorte del target primario (`calculation_week - primary.lag`); barras de igual altura (10% por decil) + N líneas de tasa de impago, una por cada target con `lag <= primary.lag` (los maduros sobre esa cohorte). Targets con lag mayor se omiten y se listan en una nota.
  - **Por target (subplots):** un panel por target activo, cada uno con su propia cohorte madura. Si la cohorte no está disponible se muestra placeholder.
- **Nuevos módulos:** `src/mlmonitor/metrics/decile_metrics.py` (`compute_decile_table` con `pd.qcut(..., duplicates="drop")` + `get_decile_data_for_segment` que carga `FACT_PERFORMANCE_INDIVIDUAL` y arma `consolidated`/`per_target`); `src/mlmonitor/report/charts.py` (matplotlib backend `Agg`, helpers `render_consolidated_decile_chart` y `render_per_target_decile_chart` que devuelven base64 PNG sin prefijo data URI — el template añade el prefijo).
- **Builder:** `_build_segment_metrics` ahora recibe `primary_target` y delega en `_build_decile_charts` que se ejecuta tras `business_table`. El `resolved_primary_target` se movió antes del loop de segmentos (antes se calculaba después).
- **`SegmentMetrics`** ganó campo `decile_charts: dict` con keys `consolidated` y `per_target` (cada uno con `img_b64`, `available`, `reason`, `cohort_week`).
- **Estilos:** clases `img.decile-chart` (max-height 9 cm, `page-break-inside: avoid`), `p.decile-note`, `p.decile-placeholder` en `styles.css`.
- **Tests:** `tests/test_decile_metrics.py` con 6 unit + 1 smoke (PNG signature). Suite total: 75/75 pasan (antes 68 + 7 nuevos).
- **Verificación:** `DB_URL=sqlite:///mlmonitor_dev.db AWS_PROFILE=nonexistent_xyz S3_BUCKET= poetry run python scripts/run_pipeline.py --date 2026-02-02 --no-email --no-llm` → PDF de 1.87 MB en `artifacts/reports/mlmonitor_2026-02-02.pdf` (vs ~150 KB antes; consistente con 22 PNGs nuevos).
- **Pendiente / siguiente:** proponer ADR §8.2.22 a `docs/decisions.md` describiendo el embedding base64, la convención de decil 1 = score bajo y la regla de inclusión de targets en la consolidada (`lag <= primary.lag`).

---

## 2026-04-29

- **Tres ajustes al reporte (PSI label, heatmap, rename).** Sesión iterativa post-screenshot del usuario:
  - **Cambio 1 (PSI Max variable label):** En la tabla "Estado por Segmento" y en el "Ranking de Urgencia" la columna de PSI Max mostraba sólo el nombre técnico (`cp_mean_ti_8_13_rez`). Ya existía el lookup en `submodel_section.html:80` ("Métricas Clave"), pero `fleet_report.html` líneas 90 y 151 no usaban `seg.variable_descriptions`. Se replicó el patrón con fallback al código técnico cuando no hay descripción en `META_VARIABLES.description`.
  - **Cambio 2 (heatmap "Métricas de Negocio por Decil"):** `get_business_metrics_table` en `metrics/business_metrics.py` ahora calcula, para cada columna `{target}_rate`, una columna paralela `{target}_color` con `rgba(239, 68, 68, alpha)` donde `alpha = 0.05 + norm * 0.80` (norm = (v - min) / (max - min) por columna del segmento). Si la columna no tiene rango (todos None o constantes), se setea None. La plantilla `submodel_section.html` aplica `style="background-color: ..."` inline a las celdas de tasa cuando hay color. Decisión consciente: inline-style sobre clases CSS dinámicas para que WeasyPrint lo respete sin ensuciar `styles.css`. Se colorean sólo las columnas de tasa; `score_bin/midpoint/total` quedan sin color.
  - **Cambio 4 (rename):** `<h1>1. Resumen Ejecutivo de Flota</h1>` → `<h1>1. Resumen Ejecutivo de Segmentos</h1>` en `fleet_report.html:42`. Se dejaron sin tocar el `<title>` (l. 6) y el subtítulo de portada (l. 21) porque el usuario fue explícito ("por ahora").
  - **Cambio 3 (Gini/KS global) — fuera de scope:** Se discutió a nivel teórico y se acordó dejarlo para otra sesión porque conlleva agregar dos métricas a la DB (`FACT_METRICS_HISTORY` u otra), lo cual toca `db/models.py` (congelado). Pendiente: ADR para definir si se persiste con `model_registry_id = NULL` o tabla nueva, y cómo normalizar el score entre segmentos (`(score_max - fnpuntaje) / score_max`) antes de pool. Plan archivado en `~/.claude/plans/lees-los-docs-de-drifting-donut.md`.
  - Verificación: 68/68 tests pasan. Pipeline `--date 2026-01-05 --no-email --no-llm` corre OK; PDF inspeccionado en `artifacts/reports/mlmonitor_2026-01-05.pdf` (157 KB). Side-effect: el pipeline subió el PDF a S3 (no hay `--no-s3` flag).
- **Limpieza de targets, descripciones inline y refresh de paleta del PDF.** Se redujo `TARGET_VARIABLES` a `b_malo4_6`, `b_malo8_13`, `b_malo14_26` (las otras 3 quedan ignoradas defensivamente desde los CSVs). Eliminada la sección "Variables del Segmento" del template `submodel_section.html` y movidas las descripciones cortas inline en "Alertas Activas" y "Métricas Clave" mediante un nuevo helper `_classify_alert(...)` en `report/builder.py` que enriquece cada alerta con `metric_kind` (PSI / Null rate / Gini / KS / Violaciones de orden / PSI Máximo) y `display_label` (descripción corta resuelta vía `variable_descriptions`).
  - Tabla "Métricas de Negocio por Decil" ahora itera sobre `context.performance_coverage` (3 columnas) en vez de hardcodear 4. Mismo loop dinámico aplicado al prompt del LLM (`analyst/prompts.py`). `bedrock_analyst.py` ahora pasa `performance_coverage` al render del segmento.
  - Mejora #1 del plan aplicada: `primary_target` movido de hardcode (`'b_malo8_13'`) a `context.primary_target`. Resolución en `builder.py`: usa `PRIMARY_TARGET` si está activo, sino el target con lag mediano. `AnalysisContext` ganó el campo `primary_target`.
  - Mejora #4 del plan aplicada: paleta nueva en `styles.css`. Portada con gradiente más profundo (`#0a0e1a → #16213e → #1a1a3e`), acentos púrpura `#8b5cf6` (logo, h1) y teal `#06b6d4` (subtítulo, h2). Badges refrescados (verde `#10b981`, ámbar `#f59e0b`, rojo `#ef4444`). `thead th` en `#1a1a3e`. `narrative-box` en azul `#3b82f6`. Clases `.priority-*` actualizadas. Tablas siguen en blanco para impresión.
  - `td/th` y `.alert-list li` ganaron `word-break: break-word` para evitar overflow con descripciones largas.
  - Fix NaN en `business_table`: ahora se hace `business_df.where(notna(), None)` antes de `to_dict(records)` para que el fallback `or 0` del template Jinja aplique (NaN es truthy y rompía el `or 0`).
  - Tests actualizados (`test_threshold_loader.py`): referencias a `b_malo2_4` / `b_malo14_52` migradas a `b_malo4_6` / `b_malo14_26`; `expected_total` derivado dinámicamente de `len(TARGET_VARIABLES)`.
  - Verificación: SQLite reset + bootstrap + ETL + pipeline OK con `--date 2026-01-05`. PDF de 28 páginas inspeccionado (portada nueva, 3 targets en metadata y tabla de negocio, alertas con formato `[CRITICAL] PSI — Promedio ever8@13 por CP 12 meses: 16.5626`, sin sección "Variables del Segmento"). 68/68 tests pasan.
  - **Side-effect a comunicar:** `run_pipeline.py` no expone `--no-s3`, así que la corrida local subió el PDF a `s3://ml-monitoring-reports-credito/mlmonitor/reports/mlmonitor_2026-01-05.pdf` (sobreescribió la versión vieja). Si esto no era deseado, hay que considerar agregar el flag y/o restaurar.
  - **Pendiente para prod:** propuesta de ADR §8.2.25 redactada en `docs/decisions.md` para cerrar SCD2 de los 3 targets descontinuados en RDS — el usuario debe aprobar antes de aplicar.
- **Nueva convención de nombres para CSVs y fix de auto-detect.** Los archivos `muestra_weekly_*` y `variables_serc_*` ahora siguen el patrón `<tipo>_<YYYYMMDD>_<segmentos>.csv` — la fecha del lunes de `semana_observacion` va **primero** tras el prefijo del tipo. El auto-detect en `run_incremental_etl.py` cambió de `sorted()[0]` a `sorted()[-1]` — con la fecha primero, el nombre más alto lexicográficamente es el más reciente sin importar qué segmentos trae el extract. Bug detectado durante ejecución: el orden anterior (`<segmentos>_<fecha>`) hacía que `s3 > s2` ganara sobre la fecha, seleccionando el archivo viejo. CSVs existentes renombrados: `S32_S41_2025_deprecated` → `20260105_s32_s41`, `S26_S52_2025` → `20260330_s26_s52`. Documentación: `docs/infrastructure/aws_deployment.md §3.2` y `data_model.md §4.1` actualizados.
- **Preparación de `muestra_weekly_s26_s52_20260330.csv` para el ETL (sin cambios de código).** El extract grande (~743K filas) traía `semana` en lugar de `semana_num`, sin `semana_observacion` ni `flg_baz_boost`. Se transformó el CSV in-place: renombrado `semana` → `semana_num`, columna constante `semana_observacion=202614` (semana 14 de 2026 = snapshot del extract), `flg_baz_boost=1` en todas las filas (universo BazBoost-only). Los targets `b_malo8_16`, `b_malo14_26`, `b_malo14_52` siguen ausentes — el ETL los omitirá con warning (esperado). Documentación: `docs/architecture/data_model.md` §0.3 (tabla ampliada `semana_sol`/`vintage_bis`, nota operativa sobre columnas manuales/alias y targets opcionales). **Operación:** si coexisten varios `muestra_weekly_*.csv` en `raw_tables/`, `run_incremental_etl.py` toma el primero en orden lexicográfico (`S26` antes que `S32`); usar `--weekly-file` para forzar el extract deseado.
- **Descripciones cortas de variables y segmentos desde CSVs diccionario.** Dos archivos entregados por crédito (`Dicionario_Variables_BB.csv` y `Dicionario_Segmentos_BB.csv`) ahora alimentan los campos `description` de `META_VARIABLES` y `model_description` de `META_MODEL_REGISTRY` durante el bootstrap. Sin cambios de schema — ambos campos ya existían.
  - Nuevas funciones `_load_variable_descriptions()` y `_load_segment_descriptions()` en `data/bootstrap.py` parsean los CSVs. Si un CSV no existe, se logea warning y se continúa sin descripciones.
  - `_populate_meta_model_registry`: `model_description` pasa de `"Segmento N — GROUP_NAME"` a la descripción corta del diccionario (ej. `"No Vinculados No HIT"`). Fallback al formato viejo si el CSV no trae el segmento.
  - `_populate_meta_variables`: `description` de variables input se pobla desde `Descripción Corta` del CSV. Variables sin entrada en el CSV quedan con `description=None` y se logea warning.
  - Descripciones de segmentos se muestran automáticamente en el PDF (el template ya usaba `seg.segment_description`).
  - Descripciones de variables se muestran en el PDF: nuevo campo `variable_descriptions` en `SegmentMetrics` (dataclass en `analyst/base.py`), cableado desde `report/builder.py`. Template `submodel_section.html` ganó una tabla "Variables del Segmento" (nombre + descripción) y muestra la descripción junto al PSI max variable.
  - Archivos tocados: `data/bootstrap.py` (loader + 2 call-sites), `analyst/base.py` (1 campo), `report/builder.py` (2 líneas), `report/templates/submodel_section.html` (tabla + PSI note).
  - Verificación: bootstrap 11 segmentos + 172 META_VARIABLES (7 con descripción corta de las 95 input — el CSV se irá llenando), 315 thresholds, 755 baseline. ETL + pipeline OK. PDF generado. 68/68 tests pasan.

---

## 2026-04-28

- **Fix `Fecha de generación` en portada del PDF.** El orchestrator pasaba `generation_date=calculation_date` al renderer, haciendo que la portada mostrara la semana de cálculo en ambos campos. Eliminado el argumento para que el renderer use `date.today()` (su default). Ahora `Fecha de generación` = fecha real de ejecución, `Semana de cálculo` = semana analizada. Cambio en `pipeline/orchestrator.py` (1 línea).
- **Consolidación del target primario en templates del reporte.** El template `fleet_report.html` tenía `26_42` hardcodeado en la portada (bug) y `b_malo8_13` hardcodeado en 3 lugares distintos (tabla de flota y ranking). Ahora hay una sola variable Jinja2 `{% set primary_target = 'b_malo8_13' %}` al inicio del template; la portada, la tabla de estado de flota y el ranking de urgencia la leen de ahí. `submodel_section.html` también se actualizó (usaba `row.b_malo8_13_rate` literal; ahora `row[primary_target ~ '_rate']`). Total: 5 sustituciones en 2 archivos. Deuda registrada en comentario: mover `primary_target` a `MetaModelRegistry` para que sea configurable por modelo.
- **Thresholds visibles en el reporte PDF.** Antes el lector no tenía forma de saber qué umbrales disparaban WARNING/CRITICAL. Cambios:
  - `analyst/base.py`: nuevo campo `thresholds: dict` en `SegmentMetrics`.
  - `report/builder.py`: carga `warning_threshold`/`critical_threshold`/`direction` de `META_METRIC_THRESHOLDS` por segmento y los pasa como `seg.thresholds` + enriquece cada alerta activa con `warn_threshold`/`crit_threshold`.
  - `report/templates/submodel_section.html`: tabla "Métricas Clave" tiene 2 columnas nuevas (Warn, Crit) con valores del segmento; lista "Alertas Activas" muestra `(umbral: warn / crit)` junto a cada alerta.
  - `report/templates/fleet_report.html`: headers de "Estado por Segmento" y "Ranking de Urgencia" ahora dicen `PSI Max (variable)`, `Gini (b_malo8_13)`, `KS (b_malo8_13)` en vez de solo "PSI Max", "Gini", "KS". La celda de PSI Max muestra el nombre de la variable entre paréntesis.
  - Total: 4 archivos tocados, 0 cambios en DB/schema.
- **Fix colores/badges hardcodeados en templates.** Los templates usaban umbrales fijos (Gini: 0.35/0.25, KS: 0.20/0.15, PSI: 0.10/0.20, ordering: 1/2) para decidir color CSS y badge, ignorando los thresholds per-segmento de `META_METRIC_THRESHOLDS`. Esto causaba que un valor como Gini=0.2073 apareciera en rojo (CRITICAL por el hardcode 0.25) aunque la DB tiene warn=0.30/crit=0.20 para ese segmento (→ WARNING). Ahora `fleet_report.html` y `submodel_section.html` leen los umbrales de `seg.thresholds` para colorear y asignar badges, con fallback a los defaults anteriores si no hay threshold.
- **Notebook `hallazgo_s5_s10_semana_2026_01_05.ipynb`.** Documenta que `variables_serc_S32_S41.csv` contiene 2 créditos con `fdregistro_solicitud` en enero 2026 (fuera del rango S32–S41 2025): crédito 49092877 (s5, 2026-01-05) y crédito 49162705 (s10, 2026-01-11). Esto causa PSI = 17.3+ al correr el pipeline con `--date 2026-01-05` (distribución de n=1 vs baseline). Pendiente de confirmar con crédito si es error del extract.

---

## 2026-04-27

- **Reemplazo del set de targets monitoreados (ADR §8.2.22).**
  - Eliminado `first_payment_default2` por completo de la DB local (SQLite) y RDS — sin SCD2-cerrado, no era de interés operativo. Borrado en orden: `FACT_METRICS_HISTORY` (44), `FACT_PERFORMANCE_BINNED` (41), `FACT_PERFORMANCE_INDIVIDUAL` (25.952), `META_METRIC_THRESHOLDS` (3 globales), `META_VARIABLES` (11 — uno por segmento).
  - Alta de `b_malo14_26` (lag 26) y `b_malo14_52` (lag 52) como targets nuevos. Insertadas 22 filas en `META_VARIABLES` (2 targets × 11 segmentos) + 6 thresholds globales (gini/ks/ordering_violations × 2 targets).
  - Código actualizado: `data/bootstrap.py::TARGET_VARIABLES` (fuente de verdad), `metrics/performance.py` (default `metric_type="b_malo2_4"`), `metrics/business_metrics.py`, `db/models.py` (comentario), `report/templates/submodel_section.html` (col FPD removida), `analyst/prompts.py` (col FPD removida del prompt).
  - Documentación: ADR `docs/decisions.md §8.2.22` agregado; `docs/architecture/data_model.md` §0.2/§0.3/§2.2/§3.2/§4.1/§4.4 actualizado; nota sobre asimetría del lag (`b_malo8_13` con lag=8) y aclaración de cómo se identifican variables intermedias (cruce `Variables_por_segmento.xlsx` ↔ `variables_serc.csv`).
  - Migración a RDS: `scripts/migrate_targets_2026_04_27.py` (idempotente, dry-run por defecto, `--apply` para ejecutar). Aplicada con éxito; segunda corrida confirma idempotencia (0 cambios).
  - Tests: 58/58 pasan.
  - Notebook de exploración creado: `notebooks/exploracion_thresholds_2026_04_27.ipynb` (10 secciones + 5 dudas al final, read-only, validado punta a punta). README de notebooks actualizado con entrada y hallazgos preliminares.
  - **Hallazgos del diff CSV vs DB:** 121 mismatches de `direction` (segmentos `bb_2..bb_11` invertidos), `b_malo8_16` faltante en los 11 segmentos del CSV, varios segmentos traen variables de scorecard que ya no están en `Variables_por_segmento.xlsx` (arrastre), 42 thresholds de variables intermedias (EXTRA_SERC), 10 INTERCEPTO. 0 duplicados, 0 inconsistencias warning/critical bajo la regla canónica.
  - **Sigue:** revisar el notebook con crédito y resolver D1–D5 antes de implementar el loader.
- **Drop columna huérfana `MetaModelRegistry.lag_semanas` (ADR §8.2.24).** Columna nunca leída por la aplicación, con `default=8` que codificaba el lag erróneo viejo. El lag operativo vive en `MetaVariables.lag_semanas` (uno por target) — semánticamente el lag es propiedad del outcome, no del modelo. Cambios:
  - `db/models.py:74`: columna eliminada.
  - `bootstrap.py:124`: removido `lag_semanas=None` del `MetaModelRegistry(...)`.
  - `tests/conftest.py:79`: removido `lag_semanas=TARGET_LAG` del fixture (era residuo).
  - `data_model.md §2.1`: nota explícita de que el lag vive en META_VARIABLES.
  - Script one-shot `migrate_drop_lag_semanas_2026_04_28.py` (idempotente, chequea `information_schema`): aplicado en RDS, segunda corrida confirma idempotencia. Borrado tras consumir.
  - Tests: 68/68 pasan; SQLite reset + bootstrap + ETL + pipeline OK.
- **Limpieza de fallbacks `or 8` para `lag_semanas`.** Los magic numbers `or 8` en `data/incremental_etl.py:103`, `metrics/calculator.py:240` y `report/builder.py:78,79,88` codificaban el lag erróneo viejo de `b_malo8_13`. Reemplazados por validación explícita: si un target tiene `lag_semanas=NULL`, se levanta `ValueError` con el nombre del target y el `registry_id` afectado. Pendiente para próxima iteración (ADR aparte): la columna huérfana `MetaModelRegistry.lag_semanas` (con `default=8`, no leída por ningún código) en `db/models.py:74`.
- **Borrados scripts one-shot ya consumidos:** `migrate_thresholds_2026_04_27.py` y `migrate_lag_b_malo8_13_2026_04_28.py`. Trazabilidad en ADR §8.2.22 (corrigendum), §8.2.23, devlog y git log.
- **Corrección del lag de `b_malo8_13` (corrigendum a ADR §8.2.22).** El target se cargó por error con `lag_semanas=8` (extremo inferior); crédito confirma que la convención correcta es siempre el extremo superior de la ventana → `lag=13`. Consecuencias del bug: con `--date 2026-01-05` y CSV `S32_S41`, la cohorte buscada (W46 de 2025) caía fuera de los datos disponibles, dejando Gini/KS de `b_malo8_13` vacíos en el PDF (el template hardcodea ese target como primario). Fix:
  - `bootstrap.py:57`: `lag_semanas=8` → `lag_semanas=13`.
  - `scripts/migrate_lag_b_malo8_13_2026_04_28.py`: idempotente, dry-run por defecto. UPDATE 11 META_VARIABLES + DELETE 38 FACT_PERFORMANCE_BINNED + DELETE 25.273 FACT_PERFORMANCE_INDIVIDUAL. Aplicado en RDS; segunda corrida confirma idempotencia.
  - Local SQLite reset: ETL detecta correctamente la cohorte W41 (origination_week 2025-10-06), genera 77 binned + 46.578 individual rows; pipeline calcula `gini_b_malo8_13` y `ks_b_malo8_13` (11 filas cada uno); PDF ahora muestra Gini/KS poblados.
  - Docs: ADR §8.2.22 cierra la duda abierta como corrigendum; `data_model.md` §0.2/§0.3/§4.1 actualizados (lag=13, convención uniforme).
  - Tests: 68/68 pasan.
- **Thresholds per-segmento desde CSV (ADR §8.2.23).** Crédito resolvió D1–D5: variables intermedias se ignoran, faltantes → default, direction canónica en código, no preservar histórico.
  - Nuevo módulo `src/mlmonitor/data/threshold_loader.py`: parsea el CSV (`bb_<n>` → `s<n>`), filtra `INTERCEPTO`/`EXTRA_SERC`/desconocidas, mapea SERC→canónico (`gini_EDAD` → `gini_edad`), aplica direction canónica en código, cae a defaults explícitos por bucket. Reusable desde bootstrap y migración.
  - `bootstrap.py::_populate_meta_metric_thresholds` refactorizado: 20 globales hardcodeadas → 315 per-segmento (1×psi + 1×null_rate + 6×3 targets + N×scorecard_var por segmento). `valid_from=2025-01-01`.
  - `metrics/calculator.py::AlertEvaluator`: `_metric_map` re-keyed a `(metric_name, model_registry_id)`; `get_metric_id` ahora exige `model_registry_id`. 6 call-sites actualizados. Fallback al global preservado para futuras métricas globales explícitas (hoy inactivo).
  - Nuevo `scripts/migrate_thresholds_2026_04_27.py` (idempotente, dry-run por defecto, `--apply`): borra `FACT_METRICS_HISTORY` entera + `META_METRIC_THRESHOLDS` entera, inserta 315 per-segmento. Borrado el `migrate_targets_2026_04_27.py` ya consumido.
  - **Migración a RDS aplicada:** 541 filas FACT_METRICS_HISTORY + 20 globales borrados; 315 per-segmento insertados. Segunda corrida confirma idempotencia ("ya migrado, salir").
  - Local SQLite reset y validado: bootstrap (315 thresholds, 0 globales) → ETL → pipeline → PDF (231 métricas).
  - Tests: 68/68 pasan (10 nuevos en `tests/test_threshold_loader.py`: direction canónica, normalización SERC, filtros, defaults, conteos por segmento, smoke contra el CSV real).
  - Documentación: ADR `docs/decisions.md §8.2.23` agregado; `docs/architecture/data_model.md §2.3` y §4.5 actualizados (de "umbrales por defecto" a "umbrales por segmento" con tabla de defaults y reglas de filtros).
- **ADR §8.2.21 implementada.** `docker/entrypoint.sh` ahora lee `RUN_DATE`, `SKIP_ETL`, `NO_EMAIL`, `NO_LLM` (env vars opcionales). Sin overrides, comportamiento idéntico al schedule semanal.
- Imagen `v0.1.1` + `latest` pusheada a ECR (`930067561911.dkr.ecr.us-east-1.amazonaws.com/mlmonitor`). Task def `mlmonitor:2` registrada apuntando a `:latest`.
- Smoke test ECS con `--overrides` (RUN_DATE=2026-01-05, SKIP_ETL=1, NO_EMAIL=1, NO_LLM=1): exit 0, 4 env vars aplicadas correctamente, pipeline corrió en ~14s sin tocar SES/Bedrock.
- Creado `scripts/backfill.py` (orquestador por subprocess, one-shot desde laptop). Inyecta `S3_BUCKET=""` para que los PDFs históricos no contaminen S3. Siempre pasa `--no-email --no-llm`.
- Módulo 12 del curso actualizado: removida la marca "no implementado aún", reemplazada por flujo real con env vars; sección de backfill apunta a `scripts/backfill.py`.
- CLAUDE.md §4 ganó una nota explicando la división ETL/pipeline (motiva por qué backfill debe correr ambos).

## 2026-04-23 (tarde — curso de AWS deployment)

- Creada carpeta `docs/curso/` con material didáctico (15 módulos + README + scripts verificadores + diagramas Mermaid + sandbox/teardown).
- Track A (inspección read-only) + Track B (recrear con sufijos `-curso-<alias>`).
- Módulo 12 responde 4 dudas operativas del usuario: re-ejecutar semana X, backfill histórico, push de cambios de código, cambios a tablas META (SCD2).
- Módulo 14 documenta 4 incidentes reales del deploy: pg_dump v14 vs RDS 16, libgdk-pixbuf rename, Docker Hub 503, SES AccessDenied en recipient.
- Siguiente posible iteración: ADR §8.2.21 para soportar `RUN_DATE` env var en `entrypoint.sh` (mejora la Opción B del módulo 12). Requiere aprobación del usuario.

## 2026-04-23

- **Migración del MVP a AWS completada.** El pipeline ahora corre en ECS Fargate, disparado manualmente con `aws ecs run-task` y semanalmente con EventBridge Scheduler (lunes 08:00 CDMX = 14:00 UTC).
- **F0 — Reset de RDS y smoke test local:**
  - `pg_dump` de la DB existente → `data/backups/rds_pre_reset_2026-04-23.sql` (987 KB).
  - `DROP TABLE CASCADE` de las 9 tablas (incluía `FACT_PERFORMANCE_OUTCOMES` no documentada; revisar si es legado o activa — ver deuda abajo).
  - `run_bootstrap.py` + `run_incremental_etl.py` + `run_pipeline.py` contra RDS desde local: PDF en S3 + correo SES entregados.
  - Subida de los 3 CSVs a `s3://ml-monitoring-reports-credito/inputs/raw_tables/` (~1 GB total).
- **F1 — Contenedor:** `mlmonitor/Dockerfile` (python:3.11-slim + libs nativas WeasyPrint + AWS CLI v2 + Poetry), `mlmonitor/docker/entrypoint.sh` (sync S3 → ETL → Pipeline), `mlmonitor/.dockerignore`. Ajuste necesario: Debian bookworm renombró `libgdk-pixbuf2.0-0` a `libgdk-pixbuf-2.0-0`. Imagen validada con `docker run` local contra RDS.
- **F2 — ECR:** repo `mlmonitor` creado. Build `linux/amd64` con `docker buildx` y push de tags `v0.1.0` + `latest` (~660 MB).
- **F3 — IAM:** roles `mlmonitor-ecs-execution` (managed `AmazonECSTaskExecutionRolePolicy`) y `mlmonitor-task` (inline policy con Secrets, Bedrock, S3 read inputs, S3 write reports, SES send). Policies JSON commiteadas en `mlmonitor/deploy/iam/`.
- **F4 — SG:** `mlmonitor-fargate-sg` (`sg-0c54b54ed399b471c`) en VPC default, solo egress. **Deuda:** el SG de RDS `sg-02e9d008b587402f7` sigue abierto 5432 a `0.0.0.0/0`; cerrarlo al SG de Fargate en próxima iteración.
- **F5 — CloudWatch:** log group `/ecs/mlmonitor`, retención 30 días.
- **F6 — ECS:** cluster `mlmonitor-cluster`, task definition `mlmonitor:1` (cpu 1024 / memory 4096, runtime X86_64, envs S3/Bedrock/INPUTS, logs a CloudWatch). JSON en `mlmonitor/deploy/taskdef.json`.
- **F7 — Smoke test ECS:** dos corridas. La primera falló en SES (`AccessDenied` sobre la identity del **destinatario**, no del sender — SES exige ambas o Condition `ses:FromAddress`). Policy actualizada para incluir ambas identities + Condition. Segunda corrida: exit 0, PDF en S3, correo entregado. Tiempo ~3:25.
- **F8 — Scheduler:** rol `mlmonitor-scheduler-invoke` y schedule `mlmonitor-weekly` con cron `0 14 ? * MON *` UTC, estado `ENABLED`. Target JSON en `mlmonitor/deploy/scheduler-target.json`. Disparo manual sigue disponible con `aws ecs run-task` directo.
- **F9 — Documentación:** ver cambios en `docs/decisions.md` (§8.2.20), nuevo `docs/infrastructure/aws_deployment.md`, cierre de dudas D1/D2/D3/D4/D5/D8 en `dudas_documentacion.md`, actualización de `CLAUDE.md §6` y `docs/architecture/architecture.md` §6/§7/§9. Archivo `docs/handoff_aws_deployment.md` eliminado al cerrar F9.
- **Deuda técnica registrada:**
  - `sg-02e9d008b587402f7` abierto 5432 a `0.0.0.0/0` — cerrarlo al SG de Fargate.
  - SES sigue en sandbox — abrir ticket para salir.
  - Terraform aún sin escribir — siguiente paso tras estabilizar MVP (usar `terraform import`).
  - CI/CD (GitHub Actions) para build + push a ECR pendiente.
  - `FACT_PERFORMANCE_OUTCOMES` aparece en RDS pero no en `db/models.py` documentado — validar si es tabla viva o residuo.
  - RDS `PubliclyAccessible=true` — mover a subnets privadas + NAT en fase de hardening.
  - D6 (refresco de baseline) y D7 (multi-modelo) siguen abiertas; no bloquean MVP.
- **Qué sigue:**
  - Subir a S3 el CSV real de la próxima semana productiva cuando toque y dejar que el Scheduler dispare solo el lunes.
  - Escribir Terraform importando lo ya creado.
  - Cerrar deuda de seguridad (SG de RDS + sandbox SES).

---

## 2026-04-22

- Limpieza de `docs/decisions.md` para dejarlo como ADR pura:
  - Eliminado §8.1 "Contexto del negocio y la data" → migrado a `docs/architecture/data_model.md` como nueva **§0 "Datos raw y contexto de negocio"** (targets/lags, columnas de `variables_serc` y `muestra_weekly`, filtros ETL, convención de fechas, flujo semanal).
  - Eliminado §8.3 "TODOs" → pendientes accionables migrados a nuevo **`docs/backlog.md`** (5 items: índice compuesto FACT_PERFORMANCE_INDIVIDUAL, tests de bootstrap/ETL, FACT_METRICS_HISTORY BI-friendly, desacoplar metric_id de SCD2, CI ambos perfiles). Observaciones de diseño movidas a `data_model.md` (score bins fijos, `fisexo` categórica, `b_malo8_16` en dev) y `architecture.md` (secuencialidad Flow A/B).
  - Eliminado §8.4 "Estado actual" entero (status + tablas de changelog por iteración): es bitácora/git log, no ADR.
  - Trimado §8.2.16 (archivo baseline) y §8.2.17 (notebook validación) a sus decisiones mínimas; detalle descriptivo movido a `data_model.md §2.4` (sub-sección "Estructura del CSV fuente") y nuevo **`notebooks/README.md`**.
  - Reordenado §8.2.19 para que siga a §8.2.18 (antes estaba al final del archivo, tras §8.4).
  - Resultado: `decisions.md` pasó de 553 líneas a ~210 y solo contiene ADRs numeradas.
- Split de `docs/infrastructure/aws_secrets_manager.md §4` (permisos IAM mínimos): narrow al scope del archivo (solo `secretsmanager:GetSecretValue`) y creación de **`docs/infrastructure/aws_iam.md`** con la matriz IAM cross-service (SM, Bedrock, S3, SES) diferenciando rol Pipeline vs rol ETL.
- Cross-refs actualizadas: `data_model.md` (§2.3, §3.3, §3.4 ahora apuntan a `backlog.md`), `architecture.md §5` (ahora a `data_model.md §0`), `CLAUDE.md` ("verificado con `poetry run pytest`" en vez de "ver DECISIONS.md §8.4").
- Nota operativa añadida a `architecture.md §3.1`: Flow A + Flow B corren secuencialmente, podrían paralelizarse pero el overhead <1 min no lo justifica.
- Qué sigue:
  - Responder las dudas D1..D8 para poder formalizar `aws_iam.md` y cerrar `architecture.md §9`.
  - Cuando haya CI, convertir los items 2 y 5 de `backlog.md` en PRs concretos.
  - Registrar en `backlog.md` cualquier nueva deuda técnica que aparezca — no volver a mezclarla con `decisions.md`.

---

## 2026-04-20

- Creé la estructura inicial de documentación del proyecto (no había `CLAUDE.md` ni carpeta `docs/`):
  - `CLAUDE.md` en raíz: identidad, stack, reglas de autonomía, convenciones, comandos, estado. Luego corregí dos imprecisiones iniciales: el default de Bedrock es Haiku 4.5 (`us.anthropic.claude-haiku-4-5-20251001-v1:0`), no Sonnet; y los tests son 58/58, no 40/41.
  - `docs/decisions.md`: copié el contenido de `DECISIONS.md` raíz y añadí la ADR §8.2.19 que documenta el descarte de la arquitectura VM+Cloud (supersede §8.2.15) — ahora todo corre en AWS desde local, pendiente de migrar la ejecución completa.
  - `DECISIONS.md` raíz ahora es stub de redirección a `docs/decisions.md`.
  - `docs/architecture/architecture.md`: componentes, diagrama textual, entry points CLI, servicios AWS, flujo semanal.
  - `docs/architecture/data_model.md`: reglas transversales (SCD2, append-only, JSONText, Lunes ISO, `origination_week` con dos semánticas) + detalle de las 8 tablas y reglas de negocio.
  - `docs/infrastructure/aws_secrets_manager.md`: inventario de `ml-monitoring/rds` y `ml-monitoring/SES`, precedencia de config, permisos IAM mínimos.
  - `dudas_documentacion.md`: archivo vivo con 8 dudas abiertas (nombre exacto del secreto SES, destinatarios, bucket S3 definitivo, plataforma AWS, origen de CSVs raw, refresco del baseline, multi-modelo, SLA).
- Verificaciones: `poetry run pytest --co -q` reporta 58 tests; `config/settings.py` confirma Bedrock Haiku 4.5 como default.
- Qué sigue:
  - Pedir al usuario que revise `dudas_documentacion.md` y resuelva los bloqueos.
  - Una vez resueltas D4, D5 y D8, ampliar `architecture.md` con el diagrama de ejecución en AWS (plataforma + disparador + origen de CSVs).
  - Evaluar si `CLAUDE.md §6` debe actualizarse cuando la migración AWS arranque.
  - Cuando se modifique algo del schema, registrarlo como nueva `§8.2.x` en `docs/decisions.md` y replicar acá el resumen.
