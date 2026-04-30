"""Tests para metrics/decile_metrics.py y report/charts.py."""

import base64

import numpy as np
import pandas as pd

from mlmonitor.metrics.decile_metrics import (
    N_DECILES,
    compute_decile_table,
)


class TestComputeDecileTable:
    def test_returns_n_deciles_for_unique_scores(self):
        scores = pd.Series(np.linspace(100, 1000, 1000))
        flags = pd.Series([1] * 100 + [0] * 900)
        out = compute_decile_table(scores, flags)
        assert len(out) == N_DECILES
        assert out["decile"].tolist() == list(range(1, 11))

    def test_event_rate_decreases_with_score(self):
        # Scores y flags inversamente correlacionados:
        # decil 1 (scores bajos) ≈ todos malos; decil 10 ≈ todos buenos.
        scores = pd.Series(np.arange(1000, dtype=float))
        flags = pd.Series([1 if s < 500 else 0 for s in range(1000)])
        out = compute_decile_table(scores, flags)
        rates = out["event_rate"].tolist()
        assert rates[0] > rates[-1]
        assert rates[0] >= 0.95
        assert rates[-1] <= 0.05

    def test_qcut_collapses_with_duplicates(self):
        scores = pd.Series([100.0] * 50 + [200.0] * 50)
        flags = pd.Series([1] * 100)
        out = compute_decile_table(scores, flags)
        # Solo 2 valores distintos → no puede formar 10 deciles.
        assert len(out) <= 2

    def test_pct_population_sums_to_one(self):
        rng = np.random.RandomState(0)
        scores = pd.Series(rng.rand(500))
        flags = pd.Series(rng.randint(0, 2, 500))
        out = compute_decile_table(scores, flags)
        assert abs(out["pct_population"].sum() - 1.0) < 1e-9

    def test_empty_input_returns_empty_df(self):
        out = compute_decile_table(pd.Series([], dtype=float), pd.Series([], dtype=int))
        assert out.empty
        assert list(out.columns) == [
            "decile", "score_min", "score_max", "score_mean",
            "n_total", "n_event", "event_rate", "pct_population",
        ]

    def test_nan_scores_dropped(self):
        scores = pd.Series([np.nan, np.nan, *np.arange(100, dtype=float)])
        flags = pd.Series([0] * 102)
        out = compute_decile_table(scores, flags)
        assert out["n_total"].sum() == 100


class TestRenderConsolidatedDecileChart:
    def test_returns_valid_png_base64(self):
        from datetime import date

        from mlmonitor.report.charts import render_consolidated_decile_chart

        table = compute_decile_table(
            pd.Series(np.arange(1000, dtype=float)),
            pd.Series([1 if s < 500 else 0 for s in range(1000)]),
        )
        rates = {"b_malo8_13": table["event_rate"].tolist()}
        b64 = render_consolidated_decile_chart(
            decile_table=table,
            rates_by_target=rates,
            cohort_week=date(2026, 1, 5),
            primary_target="b_malo8_13",
            segment_id="s1",
        )
        decoded = base64.b64decode(b64)
        # PNG signature: \x89PNG\r\n\x1a\n
        assert decoded[:8] == b"\x89PNG\r\n\x1a\n"
