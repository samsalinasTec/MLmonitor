"""
VertexAIAnalyst — Implementación usando Gemini via google-genai SDK.
"""

import json
import re

from mlmonitor.analyst.base import AnalysisContext, AnalysisResult, BaseAnalyst, SegmentMetrics
from mlmonitor.analyst.prompts import render_fleet_prompt, render_segment_prompt


class VertexAIAnalyst(BaseAnalyst):
    """Analista LLM usando Gemini en Vertex AI (via google-genai SDK)."""

    def __init__(self, project: str, location: str, model_name: str = "gemini-2.5-flash"):
        self.project = project
        self.location = location
        self.model_name = model_name
        self._client = None  # lazy init

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai

                self._client = genai.Client(
                    vertexai=True,
                    project=self.project,
                    location=self.location,
                )
            except ImportError:
                raise ImportError(
                    "google-genai no está instalado. "
                    "Instálalo con: poetry add google-genai"
                )
        return self._client

    def _call_llm(self, prompt: str) -> str:
        from google.genai import types

        client = self._get_client()
        response = client.models.generate_content(
            model=self.model_name,
            contents=[prompt],
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=8192,
            ),
        )
        return response.text

    def analyze_fleet(self, context: AnalysisContext) -> AnalysisResult:
        """Analiza la flota: 1 llamada fleet + 9 llamadas de segmento."""
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
        narrative = ""
        actions = []

        # Extraer análisis
        analysis_match = re.search(
            r"\*\*ANÁLISIS\*\*(.*?)(?:\*\*ACCIONES|```json|\Z)",
            raw,
            re.DOTALL | re.IGNORECASE,
        )
        if analysis_match:
            narrative = analysis_match.group(1).strip()
        else:
            json_start = raw.find("```json")
            if json_start > 0:
                narrative = raw[:json_start].strip()
            else:
                narrative = raw.strip()

        # Extraer acciones JSON
        json_match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
        if json_match:
            try:
                actions = json.loads(json_match.group(1))
            except json.JSONDecodeError:
                actions = []
        else:
            json_match2 = re.search(r"\[\s*\{.*?\}\s*\]", raw, re.DOTALL)
            if json_match2:
                try:
                    actions = json.loads(json_match2.group(0))
                except json.JSONDecodeError:
                    actions = []

        return narrative, actions
