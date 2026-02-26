"""
Templates Jinja2 para prompts del analista LLM.
En español, dominio-específico de scoring de crédito.

Restricciones:
- NO mencionar F1, precision, recall, AUC binario
- SÍ usar Gini, KS, Tasa de Incumplimiento, Tasa de Cumplimiento
- SIEMPRE mencionar el lag de 8 semanas al hablar de performance
- Salida con prioridades: [CRÍTICO/ALTA/MEDIA/MONITOREO]
"""

from jinja2 import Environment, BaseLoader

FLEET_SUMMARY_TEMPLATE = """\
Eres un analista experto en modelos de scoring de crédito para cartera de México.
Analiza el siguiente reporte de monitoreo de flota de scorecards y genera un resumen ejecutivo en español.

## MODELO
- ID: {{ model_id }}
- Nombre: {{ model_name }}
- Semana de cálculo: {{ calculation_week }}
- Semana de performance (con lag {{ lag_semanas }}s): {{ performance_week }}

## ESTADO GENERAL DE LA FLOTA
- Total de sub-scorecards: {{ fleet_summary.total }}
- Estado OK: {{ fleet_summary.ok }}
- Estado WARNING: {{ fleet_summary.warning }}
- Estado CRÍTICO: {{ fleet_summary.critical }}

## RESUMEN DE ALERTAS POR SEGMENTO
{% for seg in segments %}
### {{ seg.segment_id }} — {{ seg.segment_description }} [{{ seg.overall_status }}]
{% if seg.psi_max is not none %}- PSI máximo: {{ "%.3f"|format(seg.psi_max) }} en variable '{{ seg.psi_max_variable }}'{% endif %}
{% if seg.gini is not none %}- Gini: {{ "%.3f"|format(seg.gini) }} (datos con {{ lag_semanas }} semanas de lag){% endif %}
{% if seg.ks is not none %}- KS: {{ "%.3f"|format(seg.ks) }}{% endif %}
{% if seg.roll_forward_violations > 0 %}- Tasa de Incumplimiento: {{ seg.roll_forward_violations }} violaciones de ordenamiento{% endif %}
{% if seg.payment_rate_violations > 0 %}- Tasa de Cumplimiento: {{ seg.payment_rate_violations }} violaciones de ordenamiento{% endif %}
{% for alert in seg.null_rate_alerts %}- Tasa de nulos [{{ alert.label }}] en '{{ alert.variable }}': {{ "%.1f"|format(alert.rate * 100) }}%{% endfor %}
{% endfor %}

## INSTRUCCIONES
Genera un párrafo ejecutivo de 4-6 oraciones que:
1. Describa el estado general de la flota
2. Identifique los 2-3 segmentos más críticos y por qué
3. Mencione el lag estructural de {{ lag_semanas }} semanas en las métricas de performance (Gini/KS)
4. Indique la tendencia general (estable, deteriorándose, mejorando)

IMPORTANTE:
- NO menciones F1-score, precision, recall ni AUC binario
- USA los términos: Gini, KS, PSI, Tasa de Incumplimiento, Tasa de Cumplimiento
- Escribe en español técnico para un equipo de analytics

Responde ÚNICAMENTE con el párrafo ejecutivo, sin encabezados adicionales.
"""

SEGMENT_ANALYSIS_TEMPLATE = """\
Eres un analista experto en modelos de scoring de crédito para cartera de México.
Analiza el siguiente segmento y genera un análisis detallado en español.

## CONTEXTO GENERAL
- Modelo: {{ model_id }} — {{ model_name }}
- Semana de cálculo: {{ calculation_week }}
- Las métricas de performance (Gini, KS, Tasa de Incumplimiento) tienen un lag estructural de {{ lag_semanas }} semanas
- Semana de performance efectiva: {{ performance_week }}

## SEGMENTO: {{ segment_id }} — {{ segment_description }}
### Estado general: {{ overall_status }}

### Métricas de drift (datos actuales)
{% if psi_max is not none %}- PSI máximo: {{ "%.3f"|format(psi_max) }} en variable '{{ psi_max_variable }}'
  - (< 0.10 OK | 0.10-0.20 WARNING | > 0.20 CRÍTICO){% endif %}
{% for alert in null_rate_alerts %}- Tasa de nulos [{{ alert.label }}] en '{{ alert.variable }}': {{ "%.1f"|format(alert.rate * 100) }}%{% endfor %}

### Métricas de performance (con {{ lag_semanas }} semanas de lag)
{% if gini is not none %}- Gini: {{ "%.3f"|format(gini) }} (capacidad discriminativa del modelo){% endif %}
{% if ks is not none %}- KS: {{ "%.3f"|format(ks) }} (separación máxima de distribuciones){% endif %}
{% if roll_forward_violations > 0 %}- Tasa de Incumplimiento: {{ roll_forward_violations }} violación(es) de ordenamiento (score bajo = mayor incumplimiento){% endif %}
{% if payment_rate_violations > 0 %}- Tasa de Cumplimiento: {{ payment_rate_violations }} violación(es) de ordenamiento (score alto = mayor cumplimiento){% endif %}

### Tabla de negocio por decil (score ascendente = menor riesgo)
| Score Bin | Tasa Incumplimiento | Tasa Cumplimiento |
|-----------|---------------------|-------------------|
{% for row in business_table %}| {{ row.score_bin }} | {{ "%.1f"|format((row.roll_forward_rate or 0) * 100) }}% | {{ "%.1f"|format((row.payment_rate or 0) * 100) }}% |
{% endfor %}

### Alertas activas
{% if active_alerts %}{% for alert in active_alerts %}- [{{ alert.label }}] {{ alert.metric }}: {{ alert.value }}{% endfor %}{% else %}- Sin alertas activas{% endif %}

## INSTRUCCIONES
Genera:

**ANÁLISIS** (3-4 oraciones):
Describe el estado del segmento, las causas probables de las alertas y el impacto en el negocio.
Menciona el lag de {{ lag_semanas }} semanas si hablas de Gini/KS/Tasa de Incumplimiento.

**ACCIONES RECOMENDADAS** (3-5 acciones, formato JSON):
```json
[
  {
    "prioridad": "[CRÍTICO|ALTA|MEDIA|MONITOREO]",
    "accion": "Título corto de la acción",
    "detalle": "Descripción específica de qué hacer y por qué"
  }
]
```

IMPORTANTE:
- NO menciones F1-score, precision, recall ni AUC binario
- USA los términos: Gini, KS, PSI, Tasa de Incumplimiento, Tasa de Cumplimiento
- Las acciones deben ser concretas y accionables
- Si hay violaciones de ordering, menciona qué bins están involucrados
"""

_jinja_env = Environment(loader=BaseLoader())


def render_fleet_prompt(context_dict: dict) -> str:
    template = _jinja_env.from_string(FLEET_SUMMARY_TEMPLATE)
    return template.render(**context_dict)


def render_segment_prompt(context_dict: dict) -> str:
    template = _jinja_env.from_string(SEGMENT_ANALYSIS_TEMPLATE)
    return template.render(**context_dict)
