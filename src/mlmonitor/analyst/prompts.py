"""
Templates Jinja2 para prompts del analista LLM.
En español, dominio-específico de scoring de crédito.

La forma de la salida la fuerza el schema Pydantic (`schemas.py`) vía
tool_use de Bedrock — los prompts solo describen QUÉ poner en cada campo,
no la estructura del JSON.

Restricciones:
- NO mencionar F1, precision, recall, AUC binario
- SÍ usar Gini, KS, Tasa de Incumplimiento, Tasa de Cumplimiento
- SIEMPRE mencionar el lag al hablar de performance
- Prioridades válidas: CRÍTICO / ALTA / MEDIA / MONITOREO
"""

from jinja2 import Environment, BaseLoader

FLEET_SUMMARY_TEMPLATE = """\
Eres un analista experto en modelos de scoring de crédito para cartera de México.
Vas a generar un análisis estructurado en español para el siguiente reporte de
monitoreo de flota de scorecards.

## MODELO
- ID: {{ model_id }}
- Nombre: {{ model_name }}
- Semana de cálculo: {{ calculation_week }}
- Semana de performance: {{ performance_week }}

## ESTADO GENERAL DE LA FLOTA
- Total de sub-scorecards: {{ fleet_summary.total }}
- Estado OK: {{ fleet_summary.ok }}
- Estado WARNING: {{ fleet_summary.warning }}
- Estado CRÍTICO: {{ fleet_summary.critical }}

## RESUMEN DE ALERTAS POR SEGMENTO
{% for seg in segments %}
### {{ seg.segment_id }} — {{ seg.segment_description }} [{{ seg.overall_status }}]
{% if seg.psi_max is not none %}- PSI máximo: {{ "%.3f"|format(seg.psi_max) }} en variable '{{ seg.psi_max_variable }}'{% endif %}
{% for bmalo, gini_val in seg.gini.items() %}{% if gini_val is not none %}- Gini ({{ bmalo }}): {{ "%.3f"|format(gini_val) }}{% endif %}{% endfor %}
{% for bmalo, ks_val in seg.ks.items() %}{% if ks_val is not none %}- KS ({{ bmalo }}): {{ "%.3f"|format(ks_val) }}{% endif %}{% endfor %}
{% for bmalo, n_v in seg.ordering_violations.items() %}{% if n_v > 0 %}- Violaciones orden {{ bmalo }}: {{ n_v }}{% endif %}{% endfor %}
{% for alert in seg.null_rate_alerts %}- Tasa de nulos [{{ alert.label }}] en '{{ alert.variable }}': {{ "%.1f"|format(alert.rate * 100) }}%{% endfor %}
{% endfor %}

## INSTRUCCIONES
Genera el análisis llenando los campos del schema:

- `resumen_ejecutivo`: 4-6 oraciones que describan (1) el estado general de la
  flota, (2) los 2-3 segmentos más críticos y por qué, (3) mencionando la fecha
  de performance efectiva al hablar de Gini/KS, y (4) la tendencia general
  (estable, deteriorándose, mejorando).
- `segmentos_criticos`: lista con los IDs (s1..s11) de los 2-3 segmentos más
  críticos. Lista vacía si la flota está sana.

REGLAS DE CONTENIDO:
- NO menciones F1-score, precision, recall ni AUC binario.
- USA los términos: Gini, KS, PSI, Tasa de Incumplimiento (b_malo).
- Escribe en español técnico para un equipo de analytics.
"""

SEGMENT_ANALYSIS_TEMPLATE = """\
Eres un analista experto en modelos de scoring de crédito para cartera de México.
Vas a generar un análisis estructurado del siguiente segmento en español.

## CONTEXTO GENERAL
- Modelo: {{ model_id }} — {{ model_name }}
- Semana de cálculo: {{ calculation_week }}
- Semana de performance (datos pre-etiquetados): {{ performance_week }}

## SEGMENTO: {{ segment_id }} — {{ segment_description }}
### Estado general: {{ overall_status }}

### Métricas de drift (datos actuales)
{% if psi_max is not none %}- PSI máximo: {{ "%.3f"|format(psi_max) }} en variable '{{ psi_max_variable }}'
  - (< 0.10 OK | 0.10-0.20 WARNING | > 0.20 CRÍTICO){% endif %}
{% for alert in null_rate_alerts %}- Tasa de nulos [{{ alert.label }}] en '{{ alert.variable }}': {{ "%.1f"|format(alert.rate * 100) }}%{% endfor %}

### Métricas de performance por variable de outcome
{% for bmalo, gini_val in gini.items() %}{% if gini_val is not none %}- Gini ({{ bmalo }}): {{ "%.3f"|format(gini_val) }} (capacidad discriminativa){% endif %}{% endfor %}
{% for bmalo, ks_val in ks.items() %}{% if ks_val is not none %}- KS ({{ bmalo }}): {{ "%.3f"|format(ks_val) }} (separación máxima de distribuciones){% endif %}{% endfor %}
{% for bmalo, n_v in ordering_violations.items() %}{% if n_v > 0 %}- Violaciones de orden {{ bmalo }}: {{ n_v }} bin(s) fuera de secuencia{% endif %}{% endfor %}

### Tabla de negocio por decil (score ascendente = menor riesgo)
| Score Bin |{% for cov in performance_coverage %} {{ cov.target }} (%) |{% endfor %}
|-----------|{% for cov in performance_coverage %}-----------|{% endfor %}
{% for row in business_table %}| {{ row.score_bin }} |{% for cov in performance_coverage %} {{ "%.1f"|format((row[cov.target ~ '_rate'] or 0) * 100) }}% |{% endfor %}
{% endfor %}

### Alertas activas
{% if active_alerts %}{% for alert in active_alerts %}- [{{ alert.label }}] {{ alert.metric_kind }} — {{ alert.display_label }}: {{ alert.value }}
{% endfor %}{% else %}- Sin alertas activas{% endif %}

## INSTRUCCIONES
Genera el análisis llenando los campos del schema:

- `analisis`: 3-4 oraciones describiendo el estado del segmento, las causas
  probables de las alertas y el impacto en el negocio. Menciona la semana de
  performance {{ performance_week }} cuando hables de Gini/KS/Tasas de
  incumplimiento.
- `acciones`: entre 3 y 5 acciones concretas y accionables, cada una con
  prioridad CRÍTICO/ALTA/MEDIA/MONITOREO, ordenadas por prioridad descendente.
  Si hay violaciones de ordering, alguna acción debe mencionar qué bins están
  involucrados.

REGLAS DE CONTENIDO:
- NO menciones F1-score, precision, recall ni AUC binario.
- USA los términos: Gini, KS, PSI, Tasa de Incumplimiento (b_malo).
- Las acciones deben ser concretas y accionables, no genéricas.
"""

_jinja_env = Environment(loader=BaseLoader())


def render_fleet_prompt(context_dict: dict) -> str:
    template = _jinja_env.from_string(FLEET_SUMMARY_TEMPLATE)
    return template.render(**context_dict)


def render_segment_prompt(context_dict: dict) -> str:
    template = _jinja_env.from_string(SEGMENT_ANALYSIS_TEMPLATE)
    return template.render(**context_dict)
