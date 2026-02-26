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
    gini: float | None
    ks: float | None
    roll_forward_violations: int
    payment_rate_violations: int
    null_rate_alerts: list[dict]  # [{"variable": str, "rate": float, "label": str}]
    active_alerts: list[dict]     # [{"metric": str, "value": float, "label": str, ...}]
    business_table: list[dict]    # lista de deciles con roll_forward y payment_rate


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
    # fleet_summary: {"total": 9, "ok": N, "warning": N, "critical": N}


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
