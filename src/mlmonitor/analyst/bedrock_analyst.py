"""
BedrockAnalyst — Cliente LLM vía AWS Bedrock con salida estructurada.

Usa el SDK `anthropic` (`AnthropicBedrock`) con `tools=[…]` +
`tool_choice={"type": "tool", "name": …}` para forzar al modelo a llenar un
schema Pydantic. La respuesta llega en `response.content[0].input` como dict
y se valida con `model_validate`. Si el LLM no respeta el schema, se levanta
ValidationError (sin reintento) y el pipeline corre sin narrativa LLM, igual
que con `--no-llm`.
"""

import json
import logging
from typing import Type

from pydantic import BaseModel, ValidationError

from mlmonitor.analyst.base import AnalysisContext, AnalysisResult, BaseAnalyst, SegmentMetrics
from mlmonitor.analyst.prompts import render_fleet_prompt, render_segment_prompt
from mlmonitor.analyst.schemas import FleetAnalysis, SegmentAnalysis

logger = logging.getLogger(__name__)

_TOOL_NAME = "emit_analysis"
_MAX_TOKENS = 2048
_TEMPERATURE = 0.2


class BedrockAnalyst(BaseAnalyst):
    """Analista LLM con output estructurado vía tool_use."""

    def __init__(self, region: str, model_id: str):
        self.region = region
        self.model_id = model_id
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from anthropic import AnthropicBedrock
            except ImportError as e:
                raise ImportError(
                    "anthropic no está instalado. Instálalo con: "
                    "poetry install --with pipeline"
                ) from e
            self._client = AnthropicBedrock(aws_region=self.region)
        return self._client

    def _call_llm_structured(
        self, prompt: str, schema: Type[BaseModel]
    ) -> dict:
        """Llama al LLM forzando que llene `schema` vía tool_use.

        Levanta ValidationError si la respuesta no cumple el schema y
        RuntimeError si Bedrock devuelve algo distinto a un bloque tool_use.
        """
        client = self._get_client()
        tool = {
            "name": _TOOL_NAME,
            "description": (
                "Emite el análisis estructurado siguiendo exactamente el "
                "schema indicado. Todos los campos son obligatorios."
            ),
            "input_schema": schema.model_json_schema(),
        }
        response = client.messages.create(
            model=self.model_id,
            max_tokens=_MAX_TOKENS,
            temperature=_TEMPERATURE,
            tools=[tool],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": prompt}],
        )

        tool_block = next(
            (b for b in response.content if getattr(b, "type", None) == "tool_use"),
            None,
        )
        if tool_block is None:
            raise RuntimeError(
                f"Respuesta de Bedrock sin bloque tool_use (schema={schema.__name__}, "
                f"model={self.model_id}). Contenido recibido: {response.content!r}"
            )

        try:
            validated = schema.model_validate(tool_block.input)
        except ValidationError as e:
            logger.error(
                "Validación Pydantic falló para %s (model=%s): %s — input: %s",
                schema.__name__, self.model_id, e, tool_block.input,
            )
            raise

        return validated.model_dump()

    def analyze_fleet(self, context: AnalysisContext) -> AnalysisResult:
        fleet_prompt = render_fleet_prompt({
            "model_id": context.model_id,
            "model_name": context.model_name,
            "calculation_week": context.calculation_week.isoformat(),
            "performance_week": context.performance_week.isoformat(),
            "lag_semanas": context.lag_semanas,
            "fleet_summary": context.fleet_summary,
            "segments": context.segments,
        })
        fleet_data = self._call_llm_structured(fleet_prompt, FleetAnalysis)

        segment_narratives: dict[str, str] = {}
        recommended_actions: dict[str, list[dict]] = {}
        raw_responses: dict[str, str] = {
            "fleet": json.dumps(fleet_data, ensure_ascii=False, indent=2),
        }

        for segment in context.segments:
            narrative, actions = self.analyze_segment(segment, context)
            segment_narratives[segment.segment_id] = narrative
            recommended_actions[segment.segment_id] = actions
            raw_responses[segment.segment_id] = json.dumps(
                {"analisis": narrative, "acciones": actions},
                ensure_ascii=False, indent=2,
            )

        return AnalysisResult(
            fleet_narrative=fleet_data["resumen_ejecutivo"],
            segment_narratives=segment_narratives,
            recommended_actions=recommended_actions,
            raw_responses=raw_responses,
        )

    def analyze_segment(
        self, segment: SegmentMetrics, context: AnalysisContext
    ) -> tuple[str, list[dict]]:
        prompt = render_segment_prompt({
            "model_id": context.model_id,
            "model_name": context.model_name,
            "calculation_week": context.calculation_week.isoformat(),
            "performance_week": context.performance_week.isoformat(),
            "lag_semanas": context.lag_semanas,
            "segment_id": segment.segment_id,
            "segment_description": segment.segment_description,
            "overall_status": segment.overall_status,
            "psi_max": segment.psi_max,
            "psi_max_variable": segment.psi_max_variable,
            "gini": segment.gini,
            "ks": segment.ks,
            "ordering_violations": segment.ordering_violations,
            "null_rate_alerts": segment.null_rate_alerts,
            "active_alerts": segment.active_alerts,
            "business_table": segment.business_table,
            "performance_coverage": context.performance_coverage,
        })
        data = self._call_llm_structured(prompt, SegmentAnalysis)
        return data["analisis"], data["acciones"]
