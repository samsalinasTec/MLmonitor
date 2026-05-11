"""
Tests para la lógica de agregación de estado del segmento.

`_aggregate_status` distingue:
- Headline metrics (psi_score, gini_<primary>, ks_<primary>): 1 crítica → CRÍTICO inmediato.
- Agregables (resto): escalan por conteo según settings:
    `status_crit_count_to_warning`  → cuántas críticas agregables → WARNING
    `status_crit_count_to_critical` → cuántas críticas agregables → CRITICAL
    `status_warn_count_to_warning`  → cuántas warnings agregables → WARNING
- psi_max queda excluida del conteo para no doble-contar.

Estos tests están **parametrizados por los valores actuales de `config.settings`**,
no por números hardcodeados, para que sean robustos a cambios futuros de threshold
(decisión post-2026-05-05 cuando se subieron los umbrales 1/3/4 → 5/8/8).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import settings
from mlmonitor.report.builder import _aggregate_status, _is_headline_alert

PRIMARY = "b_malo8_13"


def _alert(metric: str, flag: int, kind: str = "PSI") -> dict:
    return {"metric": metric, "flag": flag, "metric_kind": kind}


def _criticas(n: int) -> list[dict]:
    """N alertas críticas agregables (psi_<varN>)."""
    return [_alert(f"psi_var_{i}", 2) for i in range(n)]


def _warnings(n: int) -> list[dict]:
    """N alertas warning agregables (psi_<varN>)."""
    return [_alert(f"psi_var_{i}", 1) for i in range(n)]


# ---------------------------------------------------------------------------
# Casos sin alertas y headlines (sin cambios respecto a la versión previa)
# ---------------------------------------------------------------------------


def test_sin_alertas_es_ok():
    status, reason = _aggregate_status([], PRIMARY)
    assert status == "OK"
    assert "sin alertas" in reason.lower()


def test_psi_score_critico_es_critical():
    alerts = [_alert("psi_score", 2, "PSI")]
    status, reason = _aggregate_status(alerts, PRIMARY)
    assert status == "CRITICAL"
    assert "headline" in reason.lower()


def test_gini_primario_critico_es_critical():
    alerts = [_alert(f"gini_{PRIMARY}", 2, "Gini")]
    status, reason = _aggregate_status(alerts, PRIMARY)
    assert status == "CRITICAL"


def test_ks_primario_critico_es_critical():
    alerts = [_alert(f"ks_{PRIMARY}", 2, "KS")]
    status, reason = _aggregate_status(alerts, PRIMARY)
    assert status == "CRITICAL"


def test_headline_warning_eleva_a_warning():
    alerts = [_alert("psi_score", 1, "PSI Score")]
    status, reason = _aggregate_status(alerts, PRIMARY)
    assert status == "WARNING"
    assert "headline" in reason.lower()


def test_headline_critico_gana_a_conteo_agregable():
    """Si hay headline crítico Y agregables críticos, prevalece el headline."""
    alerts = [
        _alert("psi_score", 2),
        *_criticas(settings.status_crit_count_to_critical),
    ]
    status, reason = _aggregate_status(alerts, PRIMARY)
    assert status == "CRITICAL"
    assert "headline" in reason.lower()


def test_is_headline_keys():
    assert _is_headline_alert("psi_score", PRIMARY) is True
    assert _is_headline_alert(f"gini_{PRIMARY}", PRIMARY) is True
    assert _is_headline_alert(f"ks_{PRIMARY}", PRIMARY) is True
    assert _is_headline_alert("psi_edad", PRIMARY) is False
    assert _is_headline_alert("null_rate_score", PRIMARY) is False
    assert _is_headline_alert("ordering_violations_b_malo8_13", PRIMARY) is False


# ---------------------------------------------------------------------------
# Tests parametrizados por settings — escalado por conteo
# ---------------------------------------------------------------------------


def test_criticas_bajo_warning_threshold_es_ok():
    """Críticas agregables por debajo del threshold de WARNING → OK."""
    n = settings.status_crit_count_to_warning - 1
    alerts = _criticas(n)
    status, _ = _aggregate_status(alerts, PRIMARY)
    assert status == "OK"


def test_criticas_iguales_al_warning_threshold_es_warning():
    """Críticas agregables == status_crit_count_to_warning → WARNING."""
    n = settings.status_crit_count_to_warning
    alerts = _criticas(n)
    status, reason = _aggregate_status(alerts, PRIMARY)
    assert status == "WARNING"
    assert str(n) in reason or "crítica" in reason.lower()


def test_criticas_entre_warning_y_critical_threshold_es_warning():
    """Críticas agregables entre warning_threshold (incl) y critical_threshold (excl) → WARNING."""
    n = settings.status_crit_count_to_critical - 1
    if n < settings.status_crit_count_to_warning:
        # Caso degenerado de configuración: skip
        return
    alerts = _criticas(n)
    status, _ = _aggregate_status(alerts, PRIMARY)
    assert status == "WARNING"


def test_criticas_iguales_al_critical_threshold_es_critical():
    """Críticas agregables == status_crit_count_to_critical → CRITICAL."""
    n = settings.status_crit_count_to_critical
    alerts = _criticas(n)
    status, _ = _aggregate_status(alerts, PRIMARY)
    assert status == "CRITICAL"


def test_warnings_bajo_warn_threshold_es_ok():
    """Warnings agregables bajo el threshold → OK."""
    n = settings.status_warn_count_to_warning - 1
    alerts = _warnings(n)
    status, _ = _aggregate_status(alerts, PRIMARY)
    assert status == "OK"


def test_warnings_iguales_al_warn_threshold_es_warning():
    """Warnings agregables == status_warn_count_to_warning → WARNING."""
    n = settings.status_warn_count_to_warning
    alerts = _warnings(n)
    status, reason = _aggregate_status(alerts, PRIMARY)
    assert status == "WARNING"
    assert "advertencia" in reason.lower()


# ---------------------------------------------------------------------------
# Casos especiales: psi_max y gini secundario
# ---------------------------------------------------------------------------


def test_psi_max_no_doble_cuenta():
    """psi_max no debe contarse junto a las críticas agregables.

    Construimos exactamente warning_threshold críticas agregables + psi_max:
    sin doble-contar, da WARNING. Si psi_max contara, daría warning_threshold+1
    (mismo nivel WARNING — empatado), lo cual no falsa la prueba; la prueba
    real es la siguiente: con (warning_threshold - 1) críticas + psi_max,
    sin doble-contar es OK; con doble-conteo sería WARNING.
    """
    n = settings.status_crit_count_to_warning - 1
    alerts = [
        _alert("psi_max", 2, "PSI Máximo"),
        *_criticas(n),
    ]
    status, _ = _aggregate_status(alerts, PRIMARY)
    # psi_max ignorado → n críticas → debe quedar OK (n < threshold)
    assert status == "OK"


def test_gini_no_primario_es_agregable():
    """Gini de target secundario NO es headline; cuenta como agregable.

    Construimos warning_threshold gini secundarios críticos (no headline):
    debe escalar a WARNING.
    """
    n = settings.status_crit_count_to_warning
    alerts = [
        _alert(f"gini_b_malo3_4_var_{i}", 2, "Gini") for i in range(n)
    ]
    # Verificamos primero que ninguno es headline:
    for a in alerts:
        assert not _is_headline_alert(a["metric"], PRIMARY)
    status, _ = _aggregate_status(alerts, PRIMARY)
    assert status == "WARNING"
