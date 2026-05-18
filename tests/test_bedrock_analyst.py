"""
Tests del BedrockAnalyst con cliente Anthropic mockeado.

Cubren las tres rutas críticas del nuevo flujo estructurado:
1. tool_use válido → dict validado por Pydantic.
2. tool_use con campos inválidos → ValidationError (sin reintento).
3. Respuesta sin bloque tool_use → RuntimeError.
4. analyze_fleet end-to-end armando AnalysisResult con el shape esperado.

No requiere boto3 ni anthropic instalados en runtime de test: parchea
_get_client para inyectar un fake.
"""

from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from mlmonitor.analyst.base import AnalysisContext, SegmentMetrics
from mlmonitor.analyst.bedrock_analyst import BedrockAnalyst
from mlmonitor.analyst.schemas import FleetAnalysis, SegmentAnalysis


def _make_block(input_payload: dict | None, block_type: str = "tool_use"):
    block = SimpleNamespace(type=block_type, input=input_payload)
    return block


def _make_response(blocks):
    return SimpleNamespace(content=blocks)


class _FakeMessages:
    def __init__(self, response):
        self._response = response
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._response


class _FakeClient:
    def __init__(self, response):
        self.messages = _FakeMessages(response)


def _make_analyst(monkeypatch, response) -> BedrockAnalyst:
    analyst = BedrockAnalyst(region="us-east-1", model_id="fake-model-id")
    fake_client = _FakeClient(response)
    monkeypatch.setattr(analyst, "_get_client", lambda: fake_client)
    return analyst


def _segment(seg_id: str = "s1", status: str = "OK") -> SegmentMetrics:
    return SegmentMetrics(
        segment_id=seg_id,
        segment_description=f"Segmento {seg_id}",
        overall_status=status,
        status_reason="",
        psi_max=0.05,
        psi_max_variable="num_var",
        gini={"b_malo": 0.42},
        ks={"b_malo": 0.25},
        ordering_violations={"b_malo": 0},
        null_rate_alerts=[],
        active_alerts=[],
        business_table=[],
    )


def _context(segments: list[SegmentMetrics]) -> AnalysisContext:
    return AnalysisContext(
        model_id="TEST_V1",
        model_name="Test",
        calculation_week=date(2026, 1, 5),
        performance_week=date(2025, 11, 10),
        lag_semanas=8,
        segments=segments,
        primary_target="b_malo",
        fleet_summary={"total": len(segments), "ok": len(segments), "warning": 0, "critical": 0},
        performance_coverage=[{"target": "b_malo", "lag": 8, "cutoff_date": date(2025, 11, 10)}],
    )


# ---------------------------------------------------------------------------

def test_call_llm_structured_returns_validated_dict(monkeypatch):
    """tool_use válido → dict que pasa Pydantic."""
    payload = {
        "analisis": "Segmento sano sin alertas relevantes.",
        "acciones": [
            {"prioridad": "MONITOREO", "accion": "Mantener vigilancia",
             "detalle": "Revisar la próxima semana."},
        ],
    }
    response = _make_response([_make_block(payload)])
    analyst = _make_analyst(monkeypatch, response)

    result = analyst._call_llm_structured("dummy prompt", SegmentAnalysis)

    assert result == payload
    # El call al cliente debe haber forzado el tool_choice correcto.
    sent = analyst._get_client().messages.last_kwargs
    assert sent["tool_choice"] == {"type": "tool", "name": "emit_analysis"}
    assert sent["tools"][0]["name"] == "emit_analysis"
    assert sent["tools"][0]["input_schema"] == SegmentAnalysis.model_json_schema()


def test_call_llm_structured_invalid_payload_raises(monkeypatch):
    """tool_use con prioridad fuera del Literal → ValidationError."""
    payload = {
        "analisis": "x",
        "acciones": [
            {"prioridad": "URGENT", "accion": "Algo", "detalle": "X"},  # inválido
        ],
    }
    response = _make_response([_make_block(payload)])
    analyst = _make_analyst(monkeypatch, response)

    with pytest.raises(ValidationError):
        analyst._call_llm_structured("dummy prompt", SegmentAnalysis)


def test_call_llm_structured_missing_tool_use_raises(monkeypatch):
    """Respuesta sin bloque tool_use → RuntimeError."""
    response = _make_response([_make_block(None, block_type="text")])
    analyst = _make_analyst(monkeypatch, response)

    with pytest.raises(RuntimeError, match="tool_use"):
        analyst._call_llm_structured("dummy prompt", SegmentAnalysis)


def test_analyze_fleet_end_to_end_shape(monkeypatch):
    """analyze_fleet arma AnalysisResult con el shape contractual del renderer.

    El fake regresa primero el FleetAnalysis y luego un SegmentAnalysis por cada
    segmento, en orden de llamada.
    """
    fleet_payload = {
        "resumen_ejecutivo": "Flota estable, sin alertas críticas en la semana.",
        "segmentos_criticos": [],
    }
    seg_payload = {
        "analisis": "Segmento sano.",
        "acciones": [
            {"prioridad": "MONITOREO", "accion": "Vigilar", "detalle": "OK"},
        ],
    }

    call_log = {"i": 0}
    responses = [
        _make_response([_make_block(fleet_payload)]),
        _make_response([_make_block(seg_payload)]),
        _make_response([_make_block(seg_payload)]),
    ]

    class _SeqMessages:
        def create(self, **kwargs):
            r = responses[call_log["i"]]
            call_log["i"] += 1
            return r

    class _SeqClient:
        messages = _SeqMessages()

    analyst = BedrockAnalyst(region="us-east-1", model_id="fake")
    monkeypatch.setattr(analyst, "_get_client", lambda: _SeqClient())

    ctx = _context([_segment("s1"), _segment("s2")])
    result = analyst.analyze_fleet(ctx)

    assert result.fleet_narrative == fleet_payload["resumen_ejecutivo"]
    assert set(result.segment_narratives.keys()) == {"s1", "s2"}
    assert result.segment_narratives["s1"] == "Segmento sano."
    assert result.recommended_actions["s1"][0]["prioridad"] == "MONITOREO"
    # raw_responses debe traer la fleet y todos los segmentos como JSON-string.
    assert "fleet" in result.raw_responses
    assert "s1" in result.raw_responses and "s2" in result.raw_responses
    # Total de llamadas = 1 fleet + N segmentos.
    assert call_log["i"] == 3
