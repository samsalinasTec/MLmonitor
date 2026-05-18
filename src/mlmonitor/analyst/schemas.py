"""
Schemas Pydantic para forzar salida estructurada del LLM vía Bedrock tool_use.

El `BedrockAnalyst` los pasa como `input_schema` de una tool y `tool_choice`
fuerza al modelo a llenarlos. El resultado se valida con `model_validate` antes
de pasarlo al renderer — si falla, se levanta ValidationError y el pipeline
queda sin narrativa LLM (no se reintenta).
"""

from typing import Literal

from pydantic import BaseModel, Field

PriorityLiteral = Literal["CRÍTICO", "ALTA", "MEDIA", "MONITOREO"]


class RecommendedAction(BaseModel):
    prioridad: PriorityLiteral = Field(
        description="Nivel de urgencia de la acción"
    )
    accion: str = Field(
        description="Título corto y accionable (máx ~80 caracteres)"
    )
    detalle: str = Field(
        description="Descripción específica de qué hacer y por qué"
    )


class SegmentAnalysis(BaseModel):
    analisis: str = Field(
        description=(
            "3-4 oraciones en español técnico describiendo el estado del "
            "segmento, las causas probables de las alertas y el impacto en el "
            "negocio. Mencionar la semana de performance al hablar de "
            "Gini/KS/Tasas de incumplimiento."
        )
    )
    acciones: list[RecommendedAction] = Field(
        description=(
            "Entre 3 y 5 acciones recomendadas, ordenadas por prioridad "
            "descendente"
        )
    )


class FleetAnalysis(BaseModel):
    resumen_ejecutivo: str = Field(
        description=(
            "4-6 oraciones en español técnico que describan el estado general "
            "de la flota, identifiquen los 2-3 segmentos más críticos, "
            "mencionen la fecha de performance efectiva y indiquen la "
            "tendencia general (estable, deteriorándose, mejorando)."
        )
    )
    segmentos_criticos: list[str] = Field(
        description=(
            "IDs de los 2-3 segmentos más críticos en formato s1..s11. "
            "Lista vacía si la flota está sana."
        )
    )
