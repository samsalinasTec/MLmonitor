"""
BedrockAnalyst — Stub para AWS Bedrock (Claude 3 Sonnet).
Implementación completa pendiente para despliegue en AWS.
"""

import json
import re

from mlmonitor.analyst.base import AnalysisContext, AnalysisResult, BaseAnalyst, SegmentMetrics
from mlmonitor.analyst.prompts import render_fleet_prompt, render_segment_prompt


class BedrockAnalyst(BaseAnalyst):
    """
    Stub de analista LLM usando AWS Bedrock (Claude 3 Sonnet).
    La lógica de llamada a la API está definida pero requiere
    credenciales AWS y boto3 instalado para funcionar.
    """

    def __init__(self, region: str, model_id: str):
        self.region = region
        self.model_id = model_id
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import boto3
                self._client = boto3.client(
                    "bedrock-runtime",
                    region_name=self.region,
                )
            except ImportError:
                raise ImportError(
                    "boto3 no está instalado. Instálalo con: pip install boto3"
                )
        return self._client

    def _call_llm(self, prompt: str) -> str:
        client = self._get_client()
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2048,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}],
        })
        response = client.invoke_model(
            modelId=self.model_id,
            body=body,
            contentType="application/json",
        )
        result = json.loads(response["body"].read())
        return result["content"][0]["text"]

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
        fleet_narrative = self._call_llm(fleet_prompt)

        segment_narratives = {}
        recommended_actions = {}
        raw_responses = {"fleet": fleet_narrative}

        for segment in context.segments:
            narrative, actions = self.analyze_segment(segment, context)
            segment_narratives[segment.segment_id] = narrative
            recommended_actions[segment.segment_id] = actions
            raw_responses[segment.segment_id] = narrative

        return AnalysisResult(
            fleet_narrative=fleet_narrative,
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
            "roll_forward_violations": segment.roll_forward_violations,
            "payment_rate_violations": segment.payment_rate_violations,
            "null_rate_alerts": segment.null_rate_alerts,
            "active_alerts": segment.active_alerts,
            "business_table": segment.business_table,
        })
        raw_response = self._call_llm(prompt)
        narrative, actions = self._parse_segment_response(raw_response)
        return narrative, actions

    def _parse_segment_response(self, raw: str) -> tuple[str, list[dict]]:
        """Extrae narrativa y acciones JSON del response del LLM."""
        narrative = raw.strip()
        actions = []

        json_match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
        if json_match:
            try:
                actions = json.loads(json_match.group(1))
                analysis_part = raw[:raw.find("```json")].strip()
                analysis_match = re.search(
                    r"\*\*ANÁLISIS\*\*(.*?)$", analysis_part, re.DOTALL | re.IGNORECASE
                )
                narrative = analysis_match.group(1).strip() if analysis_match else analysis_part
            except json.JSONDecodeError:
                pass

        return narrative, actions
