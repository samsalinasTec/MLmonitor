"""
Tests para el módulo de cálculo de PSI.

Verifica que:
- PSI se calcule correctamente desde datos puros (unit tests)
- PSI use los bins fijos de META_VARIABLES.binning_rules (no recalcula desde datos actuales)
- s1 (distribución estable) → PSI ≈ 0
- s2 (distribución drifteada) → PSI CRITICAL > 0.20
- null_rate de s2 en semana actual > umbral CRITICAL
- La ventana rodante de 4 semanas suaviza ruido y se degrada con cobertura parcial
"""

from datetime import timedelta

import pandas as pd
import pytest

from mlmonitor.db.models import FactDistributions
from mlmonitor.metrics.psi import (
    PSI_WINDOW_WEEKS,
    _aggregate_distributions_over_window,
    _window_weeks,
    compute_psi_from_df,
    get_max_psi,
    get_null_rates,
    get_psi_for_all_variables,
    get_psi_for_variable,
)


# ---------------------------------------------------------------------------
# Unit tests — sin DB
# ---------------------------------------------------------------------------

class TestComputePSI:
    def test_identical_distributions_returns_zero(self):
        """PSI = 0 cuando las distribuciones son idénticas."""
        df = pd.DataFrame({
            "bin_label": [f"bin_{i}" for i in range(10)],
            "bin_percentage": [0.1] * 10,
        })
        assert abs(compute_psi_from_df(df, df.copy())) < 1e-6

    def test_large_drift_returns_high_psi(self):
        """PSI alto cuando hay gran deriva en la distribución."""
        ref = pd.DataFrame({
            "bin_label": ["low", "mid", "high"],
            "bin_percentage": [0.70, 0.20, 0.10],
        })
        cur = pd.DataFrame({
            "bin_label": ["low", "mid", "high"],
            "bin_percentage": [0.10, 0.20, 0.70],
        })
        assert compute_psi_from_df(ref, cur) > 0.20

    def test_small_drift_returns_low_psi(self):
        """PSI bajo cuando hay poca diferencia entre distribuciones."""
        ref = pd.DataFrame({
            "bin_label": [f"b{i}" for i in range(5)],
            "bin_percentage": [0.20, 0.22, 0.18, 0.21, 0.19],
        })
        cur = pd.DataFrame({
            "bin_label": [f"b{i}" for i in range(5)],
            "bin_percentage": [0.19, 0.21, 0.20, 0.22, 0.18],
        })
        assert compute_psi_from_df(ref, cur) < 0.10

    def test_empty_df_returns_zero(self):
        ref = pd.DataFrame(columns=["bin_label", "bin_percentage"])
        cur = pd.DataFrame(columns=["bin_label", "bin_percentage"])
        assert compute_psi_from_df(ref, cur) == 0.0

    def test_psi_is_non_negative(self):
        """PSI siempre debe ser >= 0."""
        import random
        rng = random.Random(42)
        bins = [f"b{i}" for i in range(8)]

        for _ in range(20):
            ref_probs = [rng.uniform(0.05, 0.30) for _ in bins]
            s = sum(ref_probs)
            ref_probs = [p / s for p in ref_probs]

            cur_probs = [rng.uniform(0.05, 0.30) for _ in bins]
            s = sum(cur_probs)
            cur_probs = [p / s for p in cur_probs]

            ref = pd.DataFrame({"bin_label": bins, "bin_percentage": ref_probs})
            cur = pd.DataFrame({"bin_label": bins, "bin_percentage": cur_probs})

            assert compute_psi_from_df(ref, cur) >= 0.0


# ---------------------------------------------------------------------------
# Integration tests — con DB
# ---------------------------------------------------------------------------

class TestPSIFromDB:
    def test_psi_returns_float(self, session, segment_ids, variable_ids, current_week):
        """PSI calculado desde DB retorna un float válido."""
        reg_id = segment_ids["s1"]
        var_id = variable_ids["s1"]["num_var"]
        psi = get_psi_for_variable(session, reg_id, var_id, current_week)
        assert isinstance(psi, float)
        assert psi >= 0.0

    def test_s1_stable_distribution_psi_near_zero(
        self, session, segment_ids, variable_ids, current_week
    ):
        """s1 tiene distribución estable → PSI ≈ 0 (< 0.10)."""
        reg_id = segment_ids["s1"]
        var_id = variable_ids["s1"]["num_var"]
        psi = get_psi_for_variable(session, reg_id, var_id, current_week)
        assert psi < 0.10, (
            f"s1 num_var debe tener PSI < 0.10 (estable), obtenido: {psi:.4f}"
        )

    def test_s2_drifted_distribution_psi_critical(
        self, session, segment_ids, variable_ids, current_week
    ):
        """s2 tiene distribución drifteada → PSI CRITICAL (> 0.20)."""
        reg_id = segment_ids["s2"]
        var_id = variable_ids["s2"]["num_var"]
        psi = get_psi_for_variable(session, reg_id, var_id, current_week)
        assert psi > 0.20, (
            f"s2 num_var debe tener PSI > 0.20 (CRITICAL), obtenido: {psi:.4f}"
        )

    def test_all_variables_returns_dict(
        self, session, segment_ids, variable_ids, current_week
    ):
        """get_psi_for_all_variables retorna dict con todas las variables."""
        reg_id = segment_ids["s1"]
        # Solo pasar variables no-target (que tienen distribuciones)
        var_map = {
            vid: vname
            for vname, vid in variable_ids["s1"].items()
            if vname in ("num_var", "cat_var")
        }
        psi_dict = get_psi_for_all_variables(session, reg_id, var_map, current_week)
        assert isinstance(psi_dict, dict)
        assert len(psi_dict) > 0
        for var, val in psi_dict.items():
            assert isinstance(val, float), f"PSI de {var} no es float: {val}"
            assert val >= 0.0

    def test_max_psi_returns_variable_with_highest_psi(
        self, session, segment_ids, variable_ids, current_week
    ):
        """get_max_psi retorna el PSI máximo y la variable correcta."""
        reg_id = segment_ids["s2"]
        var_map = {
            vid: vname
            for vname, vid in variable_ids["s2"].items()
            if vname in ("num_var", "cat_var")
        }
        psi_dict = get_psi_for_all_variables(session, reg_id, var_map, current_week)
        max_psi, max_var = get_max_psi(psi_dict)
        assert max_psi >= 0.0
        assert max_var in psi_dict
        assert psi_dict[max_var] == max_psi
        # s2 max psi debe ser num_var (la variable drifteada)
        assert max_var == "num_var", f"Esperado max_var='num_var', obtenido: '{max_var}'"

    def test_s2_null_rate_critical(
        self, session, segment_ids, variable_ids, current_week
    ):
        """s2 num_var tiene null_count alto en semana actual → null_rate CRITICAL (> 10%)."""
        reg_id = segment_ids["s2"]
        var_map = {
            vid: vname
            for vname, vid in variable_ids["s2"].items()
            if vname in ("num_var", "cat_var")
        }
        null_rates = get_null_rates(session, reg_id, var_map, current_week)
        assert "num_var" in null_rates
        assert null_rates["num_var"] > 0.10, (
            f"s2 num_var null_rate esperado > 10% (CRITICAL), "
            f"obtenido: {null_rates['num_var']:.2%}"
        )

    def test_s1_null_rate_is_zero(
        self, session, segment_ids, variable_ids, current_week
    ):
        """s1 no tiene nulls → null_rate = 0."""
        reg_id = segment_ids["s1"]
        var_map = {
            vid: vname
            for vname, vid in variable_ids["s1"].items()
            if vname in ("num_var", "cat_var")
        }
        null_rates = get_null_rates(session, reg_id, var_map, current_week)
        if "num_var" in null_rates:
            assert null_rates["num_var"] == 0.0, (
                f"s1 no debe tener nulls, obtenido: {null_rates['num_var']:.2%}"
            )


# ---------------------------------------------------------------------------
# Rolling-window tests — ventana de 4 semanas (current + 3 previas)
# ---------------------------------------------------------------------------


def _bins(probs, total=1000):
    return [(f"bin_{i+1}", int(p * total)) for i, p in enumerate(probs)]


def _seed_week(session, reg_id, var_id, week, probs, null_count=0, total=1000):
    """Inserta una semana de FactDistributions para una variable dada."""
    rows = []
    for i, (label, count) in enumerate(_bins(probs, total)):
        rows.append(FactDistributions(
            model_registry_id=reg_id, variable_id=var_id,
            origination_week=week,
            bin_label=label, bin_count=count,
            bin_percentage=count / total,
            null_count=null_count if i == 0 else 0,
            total_records=total,
        ))
    session.add_all(rows)
    session.flush()


class TestRollingWindow:
    def test_window_helper_returns_descending_mondays(self, current_week):
        weeks = _window_weeks(current_week, PSI_WINDOW_WEEKS)
        assert len(weeks) == PSI_WINDOW_WEEKS
        assert weeks[0] == current_week
        assert weeks[-1] == current_week - timedelta(weeks=PSI_WINDOW_WEEKS - 1)
        # Descendente y separadas exactamente una semana
        for a, b in zip(weeks, weeks[1:]):
            assert (a - b).days == 7

    def test_partial_coverage_falls_back_to_existing_weeks(
        self, session, segment_ids, variable_ids, current_week
    ):
        """Con solo current_week en DB (caso de la fixture), el resultado debe
        coincidir con el PSI single-week histórico."""
        reg_id = segment_ids["s2"]
        var_id = variable_ids["s2"]["num_var"]
        psi = get_psi_for_variable(session, reg_id, var_id, current_week)
        # s2 tiene drift fuerte y solo una semana cargada → PSI > 0.20
        assert psi > 0.20

    def test_full_window_smooths_single_week_spike(
        self, session, segment_ids, variable_ids, current_week
    ):
        """Inyectar 3 semanas previas estables + spike en current_week debe
        producir un PSI estrictamente menor al PSI single-week (suavizado)."""
        reg_id = segment_ids["s1"]
        var_id = variable_ids["s1"]["num_var"]

        # PSI single-week con la fixture (s1 estable) — baseline para comparar
        psi_stable_only = get_psi_for_variable(session, reg_id, var_id, current_week)

        # Sembrar 3 semanas previas también estables (mismo perfil que s1)
        stable_probs = [0.20, 0.20, 0.20, 0.20, 0.20]
        for i in range(1, PSI_WINDOW_WEEKS):
            _seed_week(
                session, reg_id, var_id,
                current_week - timedelta(weeks=i),
                stable_probs,
            )

        psi_full_window = get_psi_for_variable(session, reg_id, var_id, current_week)
        # Con todas las semanas estables, sigue ≈ 0
        assert psi_full_window < 0.10
        # Y no debería ser mayor que el caso single-week estable
        assert psi_full_window <= psi_stable_only + 1e-6

    def test_drift_spike_attenuated_by_stable_history(
        self, session, segment_ids, variable_ids, current_week
    ):
        """Si s2 (drift fuerte en current_week) tuviera 3 semanas previas
        estables, el PSI rodante debe ser menor que el PSI sobre solo
        current_week. Demuestra la propiedad de suavizado."""
        reg_id = segment_ids["s2"]
        var_id = variable_ids["s2"]["num_var"]

        # PSI con solo current_week (drift puro, lo que hay en la fixture)
        psi_spike_only = get_psi_for_variable(
            session, reg_id, var_id, current_week, window_weeks=1,
        )

        # Sembrar 3 semanas previas estables (mismo perfil que el baseline de s2)
        stable_probs = [0.20, 0.20, 0.20, 0.20, 0.20]
        for i in range(1, PSI_WINDOW_WEEKS):
            _seed_week(
                session, reg_id, var_id,
                current_week - timedelta(weeks=i),
                stable_probs,
            )

        psi_with_history = get_psi_for_variable(
            session, reg_id, var_id, current_week, window_weeks=PSI_WINDOW_WEEKS,
        )

        assert psi_with_history < psi_spike_only, (
            f"Ventana rodante debe atenuar el spike: "
            f"single-week={psi_spike_only:.4f}, rolling={psi_with_history:.4f}"
        )

    def test_aggregation_sums_bin_counts(
        self, session, segment_ids, variable_ids, current_week
    ):
        """El helper de agregación devuelve porcentajes que suman 1 y refleja
        la suma de bin_counts, no un promedio de porcentajes."""
        reg_id = segment_ids["s1"]
        var_id = variable_ids["s1"]["num_var"]

        # Sembrar 1 semana previa con un total muy distinto (peso debe importar)
        previous_probs = [0.60, 0.10, 0.10, 0.10, 0.10]
        _seed_week(
            session, reg_id, var_id,
            current_week - timedelta(weeks=1),
            previous_probs, total=4000,  # 4× peso vs current
        )

        df = _aggregate_distributions_over_window(
            session, reg_id, var_id, current_week, window_weeks=2,
        )
        assert not df.empty
        assert abs(df["bin_percentage"].sum() - 1.0) < 1e-9
        # Con 4000 registros previos en bin_1 al 60% (=2400) y 1000 actuales
        # estables al 20% en bin_1 (=200), bin_1 debe tener (2400+200)/5000 = 0.52
        bin1 = df[df["bin_label"] == "bin_1"]["bin_percentage"].iloc[0]
        assert abs(bin1 - 0.52) < 1e-6

    def test_null_rate_uses_rolling_window(
        self, session, segment_ids, variable_ids, current_week
    ):
        """null_rate agrega null_count y total_records sobre la ventana."""
        reg_id = segment_ids["s2"]
        var_map = {
            vid: vname
            for vname, vid in variable_ids["s2"].items()
            if vname in ("num_var", "cat_var")
        }
        var_id = variable_ids["s2"]["num_var"]

        # Fixture: current_week tiene null_count=200 / total=1000 → 20%
        null_only_current = get_null_rates(
            session, reg_id, var_map, current_week, window_weeks=1,
        )["num_var"]
        assert abs(null_only_current - 0.20) < 1e-6

        # Sembrar 3 semanas previas SIN nulls, mismo total
        stable_probs = [0.20, 0.20, 0.20, 0.20, 0.20]
        for i in range(1, PSI_WINDOW_WEEKS):
            _seed_week(
                session, reg_id, var_id,
                current_week - timedelta(weeks=i),
                stable_probs, null_count=0, total=1000,
            )

        # Esperado: 200 nulls / (1000 × 4) = 0.05
        rolling = get_null_rates(
            session, reg_id, var_map, current_week, window_weeks=PSI_WINDOW_WEEKS,
        )["num_var"]
        assert abs(rolling - 0.05) < 1e-6
        assert rolling < null_only_current
