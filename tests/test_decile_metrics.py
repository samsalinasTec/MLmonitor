"""Tests para metrics/decile_metrics.py y report/charts.py."""

import base64
from datetime import date, timedelta

import numpy as np
import pandas as pd

from mlmonitor.db.models import (
    FactDecilesHistory,
    FactPerformanceIndividual,
    MetaModelRegistry,
    MetaVariables,
)
from mlmonitor.metrics.decile_metrics import (
    DECILE_WINDOW_WEEKS,
    N_DECILES,
    _window_weeks,
    compute_decile_table,
    get_decile_data_for_segment,
    persist_deciles_history,
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


class TestWindowWeeks:
    def test_returns_4_weeks_backward_in_time(self):
        cohort = date(2026, 4, 6)
        out = _window_weeks(cohort)
        assert out == [
            date(2026, 4, 6),
            date(2026, 3, 30),
            date(2026, 3, 23),
            date(2026, 3, 16),
        ]
        assert len(out) == DECILE_WINDOW_WEEKS

    def test_first_week_equals_cohort_end(self):
        cohort = date(2026, 1, 5)
        assert _window_weeks(cohort)[0] == cohort

    def test_window_size_configurable(self):
        cohort = date(2026, 4, 6)
        out = _window_weeks(cohort, window=2)
        assert out == [date(2026, 4, 6), date(2026, 3, 30)]


class TestRollingWindowDeciles:
    """Inserta un set sintético en FactPerformanceIndividual a lo largo de 5
    semanas y verifica que get_decile_data_for_segment agrega exactamente las
    4 semanas hacia atrás desde el cohorte y excluye semanas posteriores."""

    MODEL_ID = "DECILE_TEST_MODEL"
    SEGMENT_ID = "sd1"
    TARGET = "b_test_lag_8"
    LAG = 8
    CALC_WEEK = date(2026, 5, 4)
    COHORT = CALC_WEEK - timedelta(weeks=LAG)  # 2026-03-09

    def _bootstrap(self, session) -> tuple[int, list[MetaVariables]]:
        reg = MetaModelRegistry(
            model_id=self.MODEL_ID,
            submodel_id=self.SEGMENT_ID,
            model_name="decile-test",
            model_type="scorecard",
            score_min=0,
            score_max=1000,
            valid_from=date(2025, 1, 6),
            valid_to=None,
        )
        session.add(reg)
        session.flush()
        target_var = MetaVariables(
            model_registry_id=reg.id,
            variable_name=self.TARGET,
            variable_type="numeric",
            variable_rol="target",
            lag_semanas=self.LAG,
            ascending_order=False,
            valid_from=date(2025, 1, 6),
            valid_to=None,
        )
        session.add(target_var)
        session.flush()
        return reg.id, [target_var]

    def _seed_individuals(self, session, reg_id: int, week: date, n: int, score_offset: float = 0.0):
        """Inserta n créditos sintéticos en una semana dada."""
        rng = np.random.RandomState(int(week.toordinal()) % 2**31)
        rows = []
        for i in range(n):
            score = float(rng.uniform(0, 1000)) + score_offset
            flag = 1 if score < 500 else 0
            rows.append(FactPerformanceIndividual(
                credito_id=f"{week.isoformat()}_{i:04d}",
                model_registry_id=reg_id,
                origination_week=week,
                execution_week=self.CALC_WEEK,
                fnpuntaje=score,
                ventana=self.TARGET,
                flag=flag,
            ))
        session.add_all(rows)
        session.flush()

    def test_window_aggregates_4_weeks_and_excludes_future(self, populated_engine):
        from sqlalchemy.orm import sessionmaker

        SessionLocal = sessionmaker(bind=populated_engine)
        with SessionLocal() as session:
            reg_id, targets = self._bootstrap(session)

            # 4 semanas hacia atrás desde el cohorte, todas con datos
            for i in range(4):
                self._seed_individuals(session, reg_id, self.COHORT - timedelta(weeks=i), 100)
            # Una semana FUTURA al cohorte (créditos no maduros) — debe ser ignorada
            self._seed_individuals(session, reg_id, self.COHORT + timedelta(weeks=1), 100)

            session.flush()

            data = get_decile_data_for_segment(
                session=session,
                model_registry_id=reg_id,
                calculation_week=self.CALC_WEEK,
                primary_target_lag=self.LAG,
                all_targets=targets,
            )

            pt = data["per_target"][self.TARGET]
            assert pt["available"] is True
            # cohort_window_end = COHORT, start = COHORT - 3 semanas
            assert pt["cohort_window_end"] == self.COHORT
            assert pt["cohort_window_start"] == self.COHORT - timedelta(weeks=3)
            # Suma de obs = 4 × 100 = 400 (la semana futura NO entra)
            assert pt["decile_table"]["n_total"].sum() == 400

            # Rollback para no contaminar otros tests
            session.rollback()

    def test_persist_deciles_history_idempotent(self, populated_engine):
        from sqlalchemy.orm import sessionmaker

        SessionLocal = sessionmaker(bind=populated_engine)
        with SessionLocal() as session:
            reg_id, targets = self._bootstrap(session)
            for i in range(4):
                self._seed_individuals(session, reg_id, self.COHORT - timedelta(weeks=i), 100)

            data = get_decile_data_for_segment(
                session=session,
                model_registry_id=reg_id,
                calculation_week=self.CALC_WEEK,
                primary_target_lag=self.LAG,
                all_targets=targets,
            )

            n1 = persist_deciles_history(session, reg_id, self.CALC_WEEK, data)
            assert n1 > 0

            # Re-correr no duplica filas
            n2 = persist_deciles_history(session, reg_id, self.CALC_WEEK, data)
            assert n2 == n1

            count = (
                session.query(FactDecilesHistory)
                .filter(
                    FactDecilesHistory.model_registry_id == reg_id,
                    FactDecilesHistory.calculation_week == self.CALC_WEEK,
                    FactDecilesHistory.target_variable == self.TARGET,
                )
                .count()
            )
            assert count == n1

            session.rollback()


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
