"""
Tests para violaciones de orden calculadas sobre deciles reales del score
continuo (no sobre los bins fijos del scorecard).

Convención: decile 1 = scores más bajos = mayor riesgo. Para targets de
incumplimiento (ascending=False) el event_rate debe DECRECER conforme
sube el decil.
"""

import pandas as pd

from mlmonitor.metrics.decile_metrics import (
    check_decile_ordering_violations,
    load_per_target_deciles,
    persist_deciles_history,
)


def _make_decile_table(event_rates: list[float]) -> pd.DataFrame:
    """Construye una tabla de deciles sintética para los tests unitarios."""
    n = len(event_rates)
    return pd.DataFrame({
        "decile": list(range(1, n + 1)),
        "event_rate": event_rates,
        "score_min": [i * 100 for i in range(n)],
        "score_max": [(i + 1) * 100 for i in range(n)],
        "n_total": [1000] * n,
        "n_event": [int(r * 1000) if not pd.isna(r) else 0 for r in event_rates],
    })


# ---------------------------------------------------------------------------
# check_decile_ordering_violations
# ---------------------------------------------------------------------------


class TestCheckDecileOrderingViolations:
    def test_monotone_decreasing_no_violations(self):
        """Tasa de malo decreciente (ascending=False) → 0 violaciones."""
        df = _make_decile_table([0.50, 0.40, 0.30, 0.22, 0.18, 0.14, 0.10, 0.07, 0.05, 0.03])
        result = check_decile_ordering_violations(df, ascending=False)
        assert result["violations"] == 0
        assert result["violation_pairs"] == []

    def test_monotone_increasing_no_violations(self):
        """Tasa de pago creciente (ascending=True) → 0 violaciones."""
        df = _make_decile_table([0.05, 0.10, 0.18, 0.25, 0.35, 0.45, 0.55, 0.65, 0.80, 0.90])
        result = check_decile_ordering_violations(df, ascending=True)
        assert result["violations"] == 0

    def test_one_inversion_gives_one_violation(self):
        """Una sola inversión entre decil 5 y 6 → 1 violación."""
        df = _make_decile_table([0.50, 0.40, 0.30, 0.22, 0.18, 0.25, 0.10, 0.07, 0.05, 0.03])
        result = check_decile_ordering_violations(df, ascending=False)
        assert result["violations"] == 1
        pair = result["violation_pairs"][0]
        assert pair["decile_low"] == 5
        assert pair["decile_high"] == 6
        assert pair["value_low"] == 0.18
        assert pair["value_high"] == 0.25

    def test_two_separate_inversions(self):
        """Dos quiebres (caso de la imagen del usuario: decil 5 y 8)."""
        # baseline decreciente, dos saltos arriba en (5→6) y (8→9)
        df = _make_decile_table([0.50, 0.40, 0.30, 0.22, 0.18, 0.25, 0.10, 0.07, 0.12, 0.03])
        result = check_decile_ordering_violations(df, ascending=False)
        assert result["violations"] == 2
        deciles = [(p["decile_low"], p["decile_high"]) for p in result["violation_pairs"]]
        assert (5, 6) in deciles
        assert (8, 9) in deciles

    def test_tolerance_prevents_false_positives(self):
        """Diferencias menores al tol (0.005) no cuentan."""
        df = _make_decile_table([0.10] * 10)  # constante
        result = check_decile_ordering_violations(df, ascending=False)
        assert result["violations"] == 0

    def test_tolerance_custom(self):
        """tol más laxo deja pasar pequeñas inversiones."""
        df = _make_decile_table([0.50, 0.40, 0.30, 0.22, 0.18, 0.20, 0.10, 0.07, 0.05, 0.03])
        strict = check_decile_ordering_violations(df, ascending=False, tol=0.005)
        loose = check_decile_ordering_violations(df, ascending=False, tol=0.05)
        assert strict["violations"] == 1
        assert loose["violations"] == 0

    def test_nan_skipped(self):
        """NaN en event_rate se ignora; no genera violación ni la propaga."""
        rates = [0.50, 0.40, 0.30, float("nan"), 0.18, 0.14, 0.10, 0.07, 0.05, 0.03]
        df = _make_decile_table(rates)
        result = check_decile_ordering_violations(df, ascending=False)
        # Pares con NaN se saltan; el resto sigue monótono → 0
        assert result["violations"] == 0

    def test_empty_table_returns_zero(self):
        """Tabla vacía → 0 violaciones, sin error."""
        result = check_decile_ordering_violations(pd.DataFrame(), ascending=False)
        assert result == {"violations": 0, "violation_pairs": []}

    def test_none_table_returns_zero(self):
        """None de entrada → 0 violaciones (defensa contra cohortes sin datos)."""
        result = check_decile_ordering_violations(None, ascending=False)  # type: ignore[arg-type]
        assert result == {"violations": 0, "violation_pairs": []}

    def test_violation_pairs_payload_shape(self):
        """Cada par debe traer decile_low/high y value_low/high (no bin_*)."""
        df = _make_decile_table([0.40, 0.50, 0.30, 0.22, 0.18, 0.14, 0.10, 0.07, 0.05, 0.03])
        result = check_decile_ordering_violations(df, ascending=False)
        assert result["violations"] >= 1
        pair = result["violation_pairs"][0]
        assert set(pair.keys()) == {"decile_low", "decile_high", "value_low", "value_high"}
        assert isinstance(pair["decile_low"], int)
        assert isinstance(pair["decile_high"], int)


# ---------------------------------------------------------------------------
# load_per_target_deciles: roundtrip con persist_deciles_history
# ---------------------------------------------------------------------------


class TestLoadPerTargetDeciles:
    def test_empty_when_no_rows(self, session, segment_ids, current_week):
        """Sin filas en FACT_DECILES_HISTORY → dict vacío."""
        reg_id = segment_ids["s1"]
        # current_week + 100 semanas: no hay datos persistidos
        from datetime import timedelta
        future = current_week + timedelta(weeks=100)
        result = load_per_target_deciles(session, reg_id, future)
        assert result == {}

    def test_roundtrip_shape(self, session, segment_ids, current_week):
        """persist → load reproduce el shape esperado."""
        reg_id = segment_ids["s1"]
        sample = {
            "per_target": {
                "b_test_target": {
                    "cohort_week": current_week,
                    "cohort_window_start": current_week,
                    "cohort_window_end": current_week,
                    "available": True,
                    "decile_table": _make_decile_table(
                        [0.50, 0.40, 0.30, 0.22, 0.18, 0.14, 0.10, 0.07, 0.05, 0.03],
                    ).assign(
                        pct_population=lambda d: 1.0 / len(d),
                        score_mean=lambda d: (d["score_min"] + d["score_max"]) / 2,
                    ),
                }
            }
        }
        persist_deciles_history(session, reg_id, current_week, sample)
        session.flush()

        loaded = load_per_target_deciles(session, reg_id, current_week)
        assert "b_test_target" in loaded
        entry = loaded["b_test_target"]
        assert entry["available"] is True
        assert entry["reason"] is None
        assert entry["decile_table"] is not None
        df = entry["decile_table"]
        assert list(df["decile"]) == list(range(1, 11))
        assert {"event_rate", "n_total", "n_event", "score_min", "score_max"}.issubset(df.columns)
