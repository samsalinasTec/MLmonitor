"""
BaseAnalyst ABC — abstracción de proveedor LLM.

AnalysisContext: input estructurado con métricas y alertas por segmento.
AnalysisResult: output con narrativas y acciones recomendadas.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class SegmentMetrics:
    """Métricas resumidas de un segmento para el análisis LLM."""
    segment_id: str
    segment_description: str
    overall_status: str  # "OK" | "WARNING" | "CRITICAL"
    psi_max: float | None
    psi_max_variable: str | None
    gini: dict            # {b_malo_col: float | None} — Gini por variable de performance
    ks: dict              # {b_malo_col: float | None} — KS por variable de performance
    ordering_violations: dict  # {b_malo_col: int} — violaciones de monotonía por variable
    null_rate_alerts: list[dict]  # [{"variable": str, "rate": float, "label": str}]
    # active_alerts:
    #   - metric: clave técnica (ej. "psi_edad", "gini_b_malo8_13", "psi_max")
    #   - metric_kind: prefijo legible ("PSI", "PSI Máximo", "Null rate", "Gini", "KS", "Violaciones de orden")
    #   - display_label: descripción corta de la variable si aplica, sino el nombre técnico
    #   - value, label, flag, details, warn_threshold, crit_threshold
    active_alerts: list[dict]
    business_table: list[dict]    # lista de deciles con tasas b_malo por score bin
    thresholds: dict = field(default_factory=dict)  # {metric_base_name: {warn, crit, direction}}
    variable_descriptions: dict = field(default_factory=dict)  # {variable_name: short_description}


@dataclass
class AnalysisContext:
    """Contexto completo para el análisis de flota."""
    model_id: str
    model_name: str
    calculation_week: date
    performance_week: date
    lag_semanas: int
    segments: list[SegmentMetrics]
    fleet_summary: dict[str, Any] = field(default_factory=dict)
    total_submodels: int = 11  # total de sub-scorecards del modelo (para portada)
    performance_coverage: list[dict] = field(default_factory=list)   # [{target, lag, cutoff_date}]
    performance_weeks: dict[str, date] = field(default_factory=dict)  # {target_name: cutoff_date}
    primary_target: str = "b_malo8_13"   # target usado para columnas resumen de la flota
    latest_data_week: date | None = None  # ultima semana con datos en FACT_DISTRIBUTIONS
    data_lag_weeks: int | None = None     # semanas de desfase vs calendario actual


@dataclass
class AnalysisResult:
    """Resultado del análisis LLM."""
    fleet_narrative: str
    segment_narratives: dict[str, str]   # {segment_id: narrative}
    recommended_actions: dict[str, list[dict]]  # {segment_id: [{priority, action, detail}]}
    raw_responses: dict[str, str] = field(default_factory=dict)  # para debug


class BaseAnalyst(ABC):
    """Clase base para analistas LLM. Define la interfaz."""

    @abstractmethod
    def analyze_fleet(self, context: AnalysisContext) -> AnalysisResult:
        """
        Analiza la flota completa y genera narrativas + acciones.
        Realiza 1 llamada fleet summary + 9 llamadas segment narratives.
        """
        ...

    @abstractmethod
    def analyze_segment(
        self,
        segment: SegmentMetrics,
        context: AnalysisContext,
    ) -> tuple[str, list[dict]]:
        """
        Analiza un segmento individual.
        Retorna (narrative, recommended_actions).
        """
        ...

    def _determine_segment_status(self, metrics: dict[str, dict]) -> str:
        """Determina el status general de un segmento desde sus métricas."""
        statuses = [m.get("alert_label", "OK") for m in metrics.values()]
        if "CRITICAL" in statuses:
            return "CRITICAL"
        if "WARNING" in statuses:
            return "WARNING"
        return "OK"
