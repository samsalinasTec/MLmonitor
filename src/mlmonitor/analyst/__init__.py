"""
Factory para el analista LLM — siempre usa AWS Bedrock.
"""

from mlmonitor.analyst.base import (
    AnalysisContext,
    AnalysisResult,
    BaseAnalyst,
    SegmentMetrics,
)
from mlmonitor.analyst.bedrock_analyst import BedrockAnalyst


def create_analyst(**kwargs) -> BedrockAnalyst:
    """
    Crea un analista Bedrock con la configuración global.

    Args:
        **kwargs: Sobreescribir region o model_id si es necesario.

    Returns:
        Instancia de BedrockAnalyst.
    """
    from config.settings import settings

    return BedrockAnalyst(
        region=kwargs.get("region", settings.aws_region),
        model_id=kwargs.get("model_id", settings.bedrock_model_id),
    )


__all__ = [
    "create_analyst",
    "BedrockAnalyst",
    "BaseAnalyst",
    "AnalysisContext",
    "AnalysisResult",
    "SegmentMetrics",
]
