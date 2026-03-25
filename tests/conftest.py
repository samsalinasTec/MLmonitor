"""
Fixtures para tests — SQLite in-memory con datos de prueba creados inline.

Estructura de datos:
- MODEL_ID = "TEST_MODEL_V1", segmentos s1 y s2
- Inputs: num_var (numérico), cat_var (categórico)
- Target: test_target con lag_semanas=4, ascending_order=False
- WEEK_0 = semana de referencia (reference_flag=1)
- WEEK_4 = semana actual (4 semanas después = current_week)
- origination_week para test_target = WEEK_0 (current_week - lag = WEEK_4 - 4)
- execution_week para test_target = WEEK_4 (origination_week + lag)

Anomalías inyectadas:
- s1: distribución estable → PSI ≈ 0
- s2: distribución drifteada en WEEK_4 → PSI > 0.20 (CRITICAL)
- s2: null_count alto en WEEK_4 → null_rate > 10% (CRITICAL)
- s1: tasas de evento decrece con score → buena discriminación (Gini positivo)
- s2: inversión entre bins 3 y 4 → ordering violation ≥ 1
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mlmonitor.db.models import (
    Base,
    FactDistributions,
    FactPerformanceBinned,
    FactPerformanceIndividual,
    MetaMetricThresholds,
    MetaModelRegistry,
    MetaVariables,
)
from mlmonitor.db.session import get_session

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

WEEK_0 = date(2025, 1, 6)    # Semana de referencia (reference_flag=1)
MODEL_ID = "TEST_MODEL_V1"
SEGMENTS = ["s1", "s2"]
TARGET_NAME = "test_target"
TARGET_LAG = 4                # semanas de lag del target

SCORE_BINS = [
    ("0-100", 50), ("100-200", 150), ("200-300", 250), ("300-400", 350),
    ("400-500", 450), ("500-600", 550), ("600-700", 650), ("700-800", 750),
    ("800-900", 850), ("900-1000", 950),
]


def _week_date(n: int) -> date:
    return WEEK_0 + timedelta(weeks=n)


# ---------------------------------------------------------------------------
# Helpers de inserción
# ---------------------------------------------------------------------------

def _insert_registry(session):
    regs = []
    for seg_id in SEGMENTS:
        regs.append(MetaModelRegistry(
            model_id=MODEL_ID,
            submodel_id=seg_id,
            model_name="Modelo de prueba",
            model_description=f"Segmento {seg_id} — datos de test",
            model_type="scorecard",
            score_min=0, score_max=1000,
            lag_semanas=TARGET_LAG,
            valid_from=WEEK_0,
            valid_to=None,
        ))
    session.add_all(regs)
    session.flush()
    return {r.submodel_id: r.id for r in regs}


def _insert_variables(session, registry_map):
    rows = []
    for seg_id, reg_id in registry_map.items():
        rows.append(MetaVariables(
            model_registry_id=reg_id,
            variable_name="num_var",
            variable_type="numeric",
            variable_rol="input",
            binning_rules={"type": "fixed_cuts", "cuts": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]},
            woe_categories=None,
            lag_semanas=None, ascending_order=None,
            valid_from=WEEK_0, valid_to=None,
        ))
        rows.append(MetaVariables(
            model_registry_id=reg_id,
            variable_name="cat_var",
            variable_type="categorical",
            variable_rol="input",
            woe_categories=["A", "B", "C"],
            binning_rules=None,
            lag_semanas=None, ascending_order=None,
            valid_from=WEEK_0, valid_to=None,
        ))
        rows.append(MetaVariables(
            model_registry_id=reg_id,
            variable_name=TARGET_NAME,
            variable_type="numeric",
            variable_rol="target",
            lag_semanas=TARGET_LAG,
            ascending_order=False,
            woe_categories=None, binning_rules=None,
            valid_from=WEEK_0, valid_to=None,
        ))
    session.add_all(rows)
    session.flush()

    reg_reverse = {v: k for k, v in registry_map.items()}
    var_id_map = {}
    for r in rows:
        seg = reg_reverse[r.model_registry_id]
        var_id_map[(seg, r.variable_name)] = r.id
    return var_id_map


def _insert_thresholds(session):
    thresholds = [
        ("psi",                             None, 0.10, 0.20, "higher_worse"),
        ("null_rate",                       None, 0.03, 0.10, "higher_worse"),
        (f"gini_{TARGET_NAME}",             None, 0.35, 0.25, "lower_worse"),
        (f"ks_{TARGET_NAME}",               None, 0.20, 0.15, "lower_worse"),
        (f"ordering_violations_{TARGET_NAME}", None, 1,   2,   "higher_worse"),
    ]
    rows = []
    for metric, reg_id, warn, crit, direction in thresholds:
        rows.append(MetaMetricThresholds(
            metric_name=metric,
            model_registry_id=reg_id,
            warning_threshold=warn,
            critical_threshold=crit,
            direction=direction,
            valid_from=WEEK_0, valid_to=None,
        ))
    session.add_all(rows)
    session.flush()
    return {r.metric_name: r.id for r in rows}


def _insert_distributions(session, registry_map, var_id_map):
    rows = []
    current_week = _week_date(TARGET_LAG)  # WEEK_4

    for seg_id, reg_id in registry_map.items():
        num_var_id = var_id_map[(seg_id, "num_var")]
        cat_var_id = var_id_map[(seg_id, "cat_var")]

        # ---- num_var: referencia (5 bins uniformes) ----
        ref_probs = [0.20, 0.20, 0.20, 0.20, 0.20]
        for i, pct in enumerate(ref_probs):
            rows.append(FactDistributions(
                model_registry_id=reg_id, variable_id=num_var_id,
                reference_week=WEEK_0, reference_flag=1,
                bin_label=f"bin_{i+1}", bin_count=int(pct * 1000),
                bin_percentage=pct, null_count=0, total_records=1000,
            ))

        # ---- num_var: semana actual ----
        if seg_id == "s1":
            # s1: distribución estable → PSI ≈ 0
            cur_probs = [0.20, 0.20, 0.20, 0.20, 0.20]
            null_c = 0
        else:
            # s2: distribución concentrada en bin_1 → PSI CRITICAL (> 0.20)
            cur_probs = [0.80, 0.10, 0.05, 0.03, 0.02]
            null_c = 200  # 20% null rate → CRITICAL

        for i, pct in enumerate(cur_probs):
            rows.append(FactDistributions(
                model_registry_id=reg_id, variable_id=num_var_id,
                reference_week=current_week, reference_flag=0,
                bin_label=f"bin_{i+1}", bin_count=int(pct * 1000),
                bin_percentage=pct,
                null_count=null_c if i == 0 else 0,
                total_records=1000,
            ))

        # ---- cat_var: referencia y semana actual (estable) ----
        cat_probs = {"A": 0.50, "B": 0.30, "C": 0.20}
        for cat, pct in cat_probs.items():
            for wk, rflag in [(WEEK_0, 1), (current_week, 0)]:
                rows.append(FactDistributions(
                    model_registry_id=reg_id, variable_id=cat_var_id,
                    reference_week=wk, reference_flag=rflag,
                    bin_label=cat, bin_count=int(pct * 1000),
                    bin_percentage=pct, null_count=0, total_records=1000,
                ))

    session.add_all(rows)
    session.flush()


def _insert_performance_outcomes(session, registry_map, var_id_map):
    rows = []
    origination_week = WEEK_0
    execution_week = _week_date(TARGET_LAG)  # WEEK_0 + 4 = WEEK_4

    for seg_id, reg_id in registry_map.items():
        for i, (score_bin, midpoint) in enumerate(SCORE_BINS):
            count_total = 100
            if seg_id == "s1":
                # s1: buena discriminación — tasas decrecen con score
                event_rate = max(0.01, 0.80 - i * 0.08)
            else:
                # s2: inversión entre bins 3 y 4 → ordering violation
                if i == 2:
                    event_rate = 0.30   # valor bajo para bin con score medio-bajo
                elif i == 3:
                    event_rate = 0.60   # valor alto para bin con score más alto — violación
                else:
                    event_rate = max(0.01, 0.80 - i * 0.08)

            rows.append(FactPerformanceBinned(
                model_registry_id=reg_id,
                origination_week=origination_week,
                execution_week=execution_week,
                metric_type=TARGET_NAME,
                score_bin=score_bin,
                score_midpoint=midpoint,
                count_total=count_total,
                count_event_real=int(count_total * event_rate),
                sum_predicted_score=float(count_total * midpoint),
            ))

    session.add_all(rows)
    session.flush()


def _insert_performance_individual(session, registry_map):
    rows = []
    origination_iso = 202501   # ISO week corresponding to WEEK_0 (2025-W01)
    execution_iso = 202505     # origination + TARGET_LAG (4 weeks) = 2025-W05

    for seg_id, reg_id in registry_map.items():
        for i, (score_bin, midpoint) in enumerate(SCORE_BINS):
            if seg_id == "s1":
                flag = 1 if i < 5 else 0  # eventos en bins de score bajo
            else:
                flag = 1 if i in (2, 3) else 0  # inversión para testing

            rows.append(FactPerformanceIndividual(
                credito_id=f"{seg_id}_credit_{i:03d}",
                model_registry_id=reg_id,
                origination_week=origination_iso,
                execution_week=execution_iso,
                fnpuntaje=float(midpoint),
                semanas_vida=TARGET_LAG,
                ventana=TARGET_NAME,
                flag=flag,
            ))
    session.add_all(rows)
    session.flush()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def engine():
    """Engine SQLite in-memory compartido para la sesión de tests."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture(scope="session")
def populated_engine(engine):
    """Engine con todos los datos de prueba pre-cargados."""
    with get_session(engine) as session:
        registry_map = _insert_registry(session)
        var_id_map = _insert_variables(session, registry_map)
        _insert_thresholds(session)
        _insert_distributions(session, registry_map, var_id_map)
        _insert_performance_outcomes(session, registry_map, var_id_map)
        _insert_performance_individual(session, registry_map)
    return engine


@pytest.fixture
def session(populated_engine):
    """Sesión de DB para cada test."""
    SessionLocal = sessionmaker(bind=populated_engine)
    sess = SessionLocal()
    yield sess
    sess.close()


@pytest.fixture
def model_id():
    return MODEL_ID


@pytest.fixture
def current_week():
    """WEEK_4 — semana actual (4 semanas después de la referencia)."""
    return _week_date(TARGET_LAG)


@pytest.fixture
def score_week():
    """WEEK_0 — origination_week para el target con lag=4 cuando current_week=WEEK_4."""
    return WEEK_0


@pytest.fixture
def performance_week():
    """Alias de score_week — semana de origen del score correspondiente al lag del target."""
    return WEEK_0


@pytest.fixture
def segment_ids(session):
    """Retorna {submodel_id: model_registry_id}."""
    regs = (
        session.query(MetaModelRegistry)
        .filter(
            MetaModelRegistry.model_id == MODEL_ID,
            MetaModelRegistry.valid_to.is_(None),
        )
        .all()
    )
    return {r.submodel_id: r.id for r in regs}


@pytest.fixture
def variable_ids(session, segment_ids):
    """Retorna {submodel_id: {var_name: var_id}} para todos los segmentos."""
    result = {}
    for submodel_id, reg_id in segment_ids.items():
        vars_ = (
            session.query(MetaVariables)
            .filter(
                MetaVariables.model_registry_id == reg_id,
                MetaVariables.valid_to.is_(None),
            )
            .all()
        )
        result[submodel_id] = {v.variable_name: v.id for v in vars_}
    return result


@pytest.fixture
def metric_name_map(session):
    """Retorna {metric_id: metric_name} desde META_METRIC_THRESHOLDS."""
    rows = (
        session.query(MetaMetricThresholds)
        .filter(MetaMetricThresholds.valid_to.is_(None))
        .all()
    )
    return {r.id: r.metric_name for r in rows}
