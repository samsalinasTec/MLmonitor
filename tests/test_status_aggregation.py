"""
Tests para la lógica de agregación de estado del segmento.

`_aggregate_status` distingue:
- Headline metrics (psi_score, gini_<primary>, ks_<primary>): 1 crítica → CRÍTICO inmediato.
- Agregables (resto): escalan por conteo según settings (1 → ADV, 3 → CRIT, 4 warns → ADV).
- psi_max queda excluida del conteo para no doble-contar.
"""

from mlmonitor.report.builder import _aggregate_status, _is_headline_alert

PRIMARY = "b_malo8_13"


def _alert(metric: str, flag: int, kind: str = "PSI") -> dict:
    return {"metric": metric, "flag": flag, "metric_kind": kind}


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


def test_un_psi_variable_critico_es_warning():
    alerts = [_alert("psi_edad", 2, "PSI")]
    status, reason = _aggregate_status(alerts, PRIMARY)
    assert status == "WARNING"
    assert "1 crítica" in reason


def test_dos_psi_variables_criticas_es_warning():
    alerts = [_alert("psi_edad", 2), _alert("psi_ingresos", 2)]
    status, reason = _aggregate_status(alerts, PRIMARY)
    assert status == "WARNING"


def test_tres_psi_variables_criticas_es_critical():
    alerts = [
        _alert("psi_edad", 2),
        _alert("psi_ingresos", 2),
        _alert("psi_antig", 2),
    ]
    status, reason = _aggregate_status(alerts, PRIMARY)
    assert status == "CRITICAL"
    assert "3" in reason


def test_cuatro_warnings_sin_criticos_es_warning():
    alerts = [
        _alert("psi_edad", 1),
        _alert("psi_ingresos", 1),
        _alert("psi_antig", 1),
        _alert("psi_otro", 1),
    ]
    status, reason = _aggregate_status(alerts, PRIMARY)
    assert status == "WARNING"
    assert "advertencia" in reason.lower()


def test_tres_warnings_sin_criticos_es_ok():
    """Con default warn_count_to_warning=4, 3 warnings no escalan."""
    alerts = [
        _alert("psi_edad", 1),
        _alert("psi_ingresos", 1),
        _alert("psi_antig", 1),
    ]
    status, reason = _aggregate_status(alerts, PRIMARY)
    assert status == "OK"


def test_headline_warning_eleva_a_warning():
    alerts = [_alert("psi_score", 1, "PSI Score")]
    status, reason = _aggregate_status(alerts, PRIMARY)
    assert status == "WARNING"
    assert "headline" in reason.lower()


def test_psi_max_no_doble_cuenta():
    """psi_max suele aparecer junto a psi_<variable_max>; debe excluirse."""
    alerts = [
        _alert("psi_max", 2, "PSI Máximo"),
        _alert("psi_edad", 2, "PSI"),
    ]
    status, reason = _aggregate_status(alerts, PRIMARY)
    # 1 crítica agregable (psi_edad), psi_max ignorado → WARNING, no CRITICAL
    assert status == "WARNING"


def test_headline_critico_gana_a_conteo_agregable():
    """Si hay headline crítico Y agregables críticos, prevalece el headline."""
    alerts = [
        _alert("psi_score", 2),
        _alert("psi_edad", 2),
        _alert("psi_ingresos", 2),
    ]
    status, reason = _aggregate_status(alerts, PRIMARY)
    assert status == "CRITICAL"
    assert "headline" in reason.lower()


def test_gini_no_primario_es_agregable():
    """Gini de target secundario NO es headline."""
    alerts = [_alert("gini_b_malo3_4", 2, "Gini")]
    assert not _is_headline_alert("gini_b_malo3_4", PRIMARY)
    status, reason = _aggregate_status(alerts, PRIMARY)
    # 1 crítica agregable → WARNING (no CRITICAL)
    assert status == "WARNING"


def test_is_headline_keys():
    assert _is_headline_alert("psi_score", PRIMARY) is True
    assert _is_headline_alert(f"gini_{PRIMARY}", PRIMARY) is True
    assert _is_headline_alert(f"ks_{PRIMARY}", PRIMARY) is True
    assert _is_headline_alert("psi_edad", PRIMARY) is False
    assert _is_headline_alert("null_rate_score", PRIMARY) is False
    assert _is_headline_alert("ordering_violations_b_malo8_13", PRIMARY) is False
