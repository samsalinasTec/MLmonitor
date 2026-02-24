"""
Factory para analistas LLM. Selecciona el proveedor via LLM_PROVIDER.
"""

from mlmonitor.analyst.base import (
    AnalysisContext,
    AnalysisResult,
    BaseAnalyst,
    SegmentMetrics,
)


def create_analyst(provider: str | None = None, **kwargs) -> BaseAnalyst:
    """
    Factory que crea el analista según LLM_PROVIDER.

    Args:
        provider: "vertex" | "bedrock" (default: settings.llm_provider)
        **kwargs: parámetros específicos del proveedor

    Returns:
        Instancia del analista correspondiente.
    """
    if provider is None:
        from config.settings import settings
        provider = settings.llm_provider

    if provider == "vertex":
        from config.settings import settings
        from mlmonitor.analyst.vertex_analyst import VertexAIAnalyst

        return VertexAIAnalyst(
            project=kwargs.get("project", settings.google_cloud_project),
            location=kwargs.get("location", settings.google_cloud_location),
            model_name=kwargs.get("model_name", settings.google_cloud_model),
        )

    elif provider == "bedrock":
        from config.settings import settings
        from mlmonitor.analyst.bedrock_analyst import BedrockAnalyst

        return BedrockAnalyst(
            region=kwargs.get("region", settings.aws_region),
            model_id=kwargs.get("model_id", settings.bedrock_model_id),
        )

    else:
        raise ValueError(
            f"Proveedor LLM no soportado: '{provider}'. "
            "Usa 'vertex' o 'bedrock'."
        )


__all__ = [
    "create_analyst",
    "BaseAnalyst",
    "AnalysisContext",
    "AnalysisResult",
    "SegmentMetrics",
]
