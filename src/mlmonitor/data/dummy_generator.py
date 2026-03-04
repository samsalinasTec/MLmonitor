"""
DummyDataGenerator — Popula las 6 tablas con data de prueba para CRÉDITO (originación).

Caso: BazBoost / modelo de score de crédito con 11 segmentos (s1–s11).
Timeline (referencia: 2026-02-16):
- Semana 0  (2025-08-18): reference_flag=True, baseline de entrenamiento
- Semanas 1-8  (sep-oct 2025): distribuciones + outcomes (ya pasaron 8 semanas)
- Semanas 9-20 (oct 2025 - ene 2026): solo distribuciones, sin outcomes aún

Anomalías inyectadas:
- s3: PSI nivel_endeudamiento = 0.25+ CRITICAL (semanas 17-20)
- s5: PSI capacidad_pago = 0.14  WARNING  (semanas 15-20)
- s4: Ordering violation (semanas 7-8)
- s1: Gini cae 0.45 → 0.28 gradualmente (semanas 1-8)
- s9: null_count alto en meses_en_buro (semanas 18-20)
"""

import math
import random
from datetime import date, datetime, timedelta

import numpy as np
from sqlalchemy.orm import Session

from mlmonitor.db.models import (
    FactDistributions,
    FactPerformanceOutcomes,
    MetaMetricThresholds,
    MetaModelRegistry,
    MetaVariables,
)

MODEL_ID = "BAZBOOST_V1"

# 11 segmentos del modelo de crédito (originación)
SEGMENTS = {
    "s1": "No file - sin historial crediticio",
    "s2": "In file < 6 meses",
    "s3": "In file >= 6 meses",
    "s4": "Big file - asalariado",
    "s5": "Big file - capta con Buró",
    "s6": "Big file - capta sin Buró",
    "s7": "Asalariado - nómina/portabilidad",
    "s8": "Capta - con recursos",
    "s9": "Capta - sin Buró",
    "s10": "Capta - con Buró",
    "s11": "Comodín - resto",
}

VARIABLES = {
    "numeric": [
        "capacidad_pago",
        "nivel_endeudamiento",
        "antiguedad_relacion",
        "ingresos_declarados",
        "num_productos_activos",
        "meses_en_buro",
    ],
    "categorical": [
        "tipo_ocupacion",
        "region_geografica",
    ],
}

TIPO_OCUPACION_CATS = ["Empleado", "Independiente", "PyME", "Profesional", "Otro"]
REGION_GEOGRAFICA_CATS = ["Norte", "Noreste", "Centro", "Sur", "Sureste"]

SCORE_BINS = [
    "0-100", "100-200", "200-300", "300-400", "400-500",
    "500-600", "600-700", "700-800", "800-900", "900-1000",
]
SCORE_MIDPOINTS = [50, 150, 250, 350, 450, 550, 650, 750, 850, 950]

REFERENCE_DATE = date(2025, 8, 18)  # Semana 0
WEEK_DELTA = timedelta(weeks=1)

NUM_BINS_NUMERIC = 10


def _week_date(week_num: int) -> date:
    return REFERENCE_DATE + week_num * WEEK_DELTA


class DummyDataGenerator:
    def __init__(self, session: Session, seed: int = 42):
        self.session = session
        self.rng = random.Random(seed)
        np.random.seed(seed)

        # Mapas de IDs surrogados — se pueblan en run()
        self._registry_map: dict[str, int] = {}   # fleet_id → model_registry_id
        self._variable_map: dict[tuple, int] = {}  # (fleet_id, var_name) → variable_id
        self._metric_map: dict[str, int] = {}       # metric_name → metric_id

    def run(self) -> dict[str, int]:
        """Ejecuta la generación completa. Retorna conteo de filas por tabla."""
        counts = {}
        counts["META_MODEL_REGISTRY"] = self._populate_meta_model_registry()
        counts["META_VARIABLES"] = self._populate_meta_variables()
        counts["META_METRIC_THRESHOLDS"] = self._populate_meta_metric_thresholds()
        counts["FACT_DISTRIBUTIONS"] = self._populate_fact_distributions()
        counts["FACT_PERFORMANCE_OUTCOMES"] = self._populate_fact_performance_outcomes()
        return counts

    # ------------------------------------------------------------------
    # META tables
    # ------------------------------------------------------------------

    def _populate_meta_model_registry(self) -> int:
        rows = []
        for seg_id, seg_desc in SEGMENTS.items():
            rows.append(MetaModelRegistry(
                model_id=MODEL_ID,
                fleet_id=seg_id,                        # fleet_id identifica el sub-modelo
                model_name="BazBoost Crédito",
                model_description=seg_desc,             # descripción del segmento
                model_type="scorecard",
                target_definition="Probabilidad de incumplimiento en ventana de 8 semanas",
                score_min=0,
                score_max=1000,
                lag_semanas=8,
                feature_count=8,
                training_cutoff_date=date(2025, 7, 31),
                owner_team="Equipo Analytics Crédito",
                valid_from=date(2025, 1, 1),
                valid_to=None,
            ))
        self.session.add_all(rows)
        self.session.flush()

        # Capturar IDs surrogados
        self._registry_map = {r.fleet_id: r.id for r in rows}
        return len(rows)

    def _populate_meta_variables(self) -> int:
        rows = []
        for seg_id in SEGMENTS:
            reg_id = self._registry_map[seg_id]
            for vname in VARIABLES["numeric"]:
                rows.append(MetaVariables(
                    model_registry_id=reg_id,
                    variable_name=vname,
                    variable_type="numeric",
                    variable_rol="input",
                    description=f"Variable numérica: {vname}",
                    woe_categories=None,
                    binning_rules={"type": "quantile", "n_bins": NUM_BINS_NUMERIC},
                    source_table="MA_B.tbl_variables_credito",
                    valid_from=date(2025, 1, 1),
                    valid_to=None,
                ))
            for vname in VARIABLES["categorical"]:
                cats = TIPO_OCUPACION_CATS if vname == "tipo_ocupacion" else REGION_GEOGRAFICA_CATS
                rows.append(MetaVariables(
                    model_registry_id=reg_id,
                    variable_name=vname,
                    variable_type="categorical",
                    variable_rol="input",
                    description=f"Variable categórica: {vname}",
                    woe_categories=cats,
                    binning_rules=None,
                    source_table="MA_B.tbl_variables_credito",
                    valid_from=date(2025, 1, 1),
                    valid_to=None,
                ))
        self.session.add_all(rows)
        self.session.flush()

        # Capturar IDs surrogados de variables
        for r in rows:
            # fleet_id inverso desde registry_map
            fleet_id = next(k for k, v in self._registry_map.items() if v == r.model_registry_id)
            self._variable_map[(fleet_id, r.variable_name)] = r.id

        return len(rows)

    def _populate_meta_metric_thresholds(self) -> int:
        rows = []
        # Umbrales globales (model_registry_id=None)
        global_thresholds = [
            ("psi", 0.10, 0.20, "higher_worse"),
            ("gini", 0.35, 0.25, "lower_worse"),
            ("ks", 0.20, 0.15, "lower_worse"),
            ("roll_forward_ordering_violations", 1, 2, "higher_worse"),
            ("payment_rate_ordering_violations", 1, 2, "higher_worse"),
            ("null_rate", 0.03, 0.10, "higher_worse"),
        ]
        for metric, warn, crit, direction in global_thresholds:
            rows.append(MetaMetricThresholds(
                metric_name=metric,
                model_registry_id=None,   # NULL = umbral global
                warning_threshold=warn,
                critical_threshold=crit,
                direction=direction,
                valid_from=date(2025, 1, 1),
                valid_to=None,
            ))
        self.session.add_all(rows)
        self.session.flush()

        # Capturar IDs
        self._metric_map = {r.metric_name: r.id for r in rows}
        return len(rows)

    # ------------------------------------------------------------------
    # FACT_DISTRIBUTIONS
    # ------------------------------------------------------------------

    def _populate_fact_distributions(self) -> int:
        total = 0
        # Semana 0: referencia de entrenamiento
        total += self._insert_distributions(week=0, reference_flag=True)
        # Semanas 1-20: datos de producción
        for week in range(1, 21):
            total += self._insert_distributions(week=week, reference_flag=False)
        return total

    def _insert_distributions(self, week: int, reference_flag: bool) -> int:
        ref_week = _week_date(week)
        rows = []

        for seg_id in SEGMENTS:
            reg_id = self._registry_map[seg_id]
            total_records = self.rng.randint(2000, 8000)

            # Numéricas
            for vname in VARIABLES["numeric"]:
                var_id = self._variable_map[(seg_id, vname)]
                base_probs = self._get_base_dist(vname, seg_id)
                probs = self._apply_drift(base_probs, vname, seg_id, week)
                counts = self._probs_to_counts(probs, total_records)

                for bin_idx in range(NUM_BINS_NUMERIC):
                    bin_label = f"bin_{bin_idx + 1}"
                    null_count = 0
                    # s9: alto null_count en meses_en_buro semanas 18-20
                    if seg_id == "s9" and vname == "meses_en_buro" and week >= 18:
                        null_count = int(total_records * self.rng.uniform(0.12, 0.18))

                    rows.append(FactDistributions(
                        model_registry_id=reg_id,
                        variable_id=var_id,
                        reference_week=ref_week,
                        reference_flag=1 if reference_flag else 0,
                        bin_label=bin_label,
                        bin_count=counts[bin_idx],
                        bin_percentage=round(probs[bin_idx], 6),
                        null_count=null_count,
                        sum_value=None,
                        total_records=total_records,
                    ))

            # Categóricas
            for vname in VARIABLES["categorical"]:
                var_id = self._variable_map[(seg_id, vname)]
                cats = TIPO_OCUPACION_CATS if vname == "tipo_ocupacion" else REGION_GEOGRAFICA_CATS
                base_probs = [1.0 / len(cats)] * len(cats)
                probs = self._apply_cat_drift(base_probs, vname, seg_id, week)
                counts = self._probs_to_counts(probs, total_records)

                for i, cat in enumerate(cats):
                    rows.append(FactDistributions(
                        model_registry_id=reg_id,
                        variable_id=var_id,
                        reference_week=ref_week,
                        reference_flag=1 if reference_flag else 0,
                        bin_label=cat,
                        bin_count=counts[i],
                        bin_percentage=round(probs[i], 6),
                        null_count=0,
                        sum_value=None,
                        total_records=total_records,
                    ))

        self.session.add_all(rows)
        self.session.flush()
        return len(rows)

    def _get_base_dist(self, vname: str, seg_id: str) -> list[float]:
        """Distribución base por variable (uniforme con variación por segmento)."""
        seed_val = hash((vname, seg_id)) % 10000
        rng = np.random.default_rng(seed_val)
        probs = rng.dirichlet(np.ones(NUM_BINS_NUMERIC) * 2.0)
        return probs.tolist()

    def _apply_drift(
        self, base_probs: list[float], vname: str, seg_id: str, week: int
    ) -> list[float]:
        """Aplica drift según las anomalías definidas."""
        probs = list(base_probs)

        # s3: drift CRÍTICO en nivel_endeudamiento semanas 17-20
        if seg_id == "s3" and vname == "nivel_endeudamiento" and week >= 17:
            drift_factor = min(1.0, (week - 16) * 0.25)
            # Concentrar en bins altos
            for i in range(NUM_BINS_NUMERIC):
                if i >= 7:
                    probs[i] = base_probs[i] * (1 + drift_factor * 3.0)
                else:
                    probs[i] = base_probs[i] * max(0.1, 1 - drift_factor * 0.8)

        # s5: drift WARNING en capacidad_pago semanas 15-20
        elif seg_id == "s5" and vname == "capacidad_pago" and week >= 15:
            drift_factor = min(1.0, (week - 14) * 0.20)
            for i in range(NUM_BINS_NUMERIC):
                if i >= 6:
                    probs[i] = base_probs[i] * (1 + drift_factor * 1.5)
                else:
                    probs[i] = base_probs[i] * max(0.1, 1 - drift_factor * 0.4)

        # Normalizar
        total = sum(probs)
        return [p / total for p in probs]

    def _apply_cat_drift(
        self, base_probs: list[float], vname: str, seg_id: str, week: int
    ) -> list[float]:
        """Aplica drift leve en variables categóricas (sin anomalías específicas)."""
        rng = np.random.default_rng(hash((vname, seg_id, week)) % 100000)
        noise = rng.dirichlet(np.ones(len(base_probs)) * 20.0)
        probs = [0.9 * b + 0.1 * n for b, n in zip(base_probs, noise)]
        total = sum(probs)
        return [p / total for p in probs]

    def _probs_to_counts(self, probs: list[float], total: int) -> list[int]:
        counts = [int(p * total) for p in probs]
        diff = total - sum(counts)
        counts[0] += diff
        return counts

    # ------------------------------------------------------------------
    # FACT_PERFORMANCE_OUTCOMES
    # ------------------------------------------------------------------

    def _populate_fact_performance_outcomes(self) -> int:
        total = 0
        # Solo semanas 1-8: outcomes disponibles tras el lag de 8 semanas
        for week in range(1, 9):
            total += self._insert_performance_outcomes(week=week)
        return total

    def _insert_performance_outcomes(self, week: int) -> int:
        date_score_key = _week_date(week)
        date_outcome_key = date_score_key + timedelta(weeks=8)
        rows = []

        for seg_id in SEGMENTS:
            reg_id = self._registry_map[seg_id]
            for bin_idx, (score_bin, midpoint) in enumerate(
                zip(SCORE_BINS, SCORE_MIDPOINTS)
            ):
                count_total = self.rng.randint(200, 1200)
                roll_fwd = self._get_roll_forward(seg_id, bin_idx, week)
                pay_rate = self._get_payment_rate(seg_id, bin_idx, week)
                count_roll_fwd = int(count_total * roll_fwd)
                count_payment = int(count_total * pay_rate)
                sum_predicted_score = count_total * midpoint  # aproximación

                rows.append(FactPerformanceOutcomes(
                    model_registry_id=reg_id,
                    date_score_key=date_score_key,
                    date_outcome_key=date_outcome_key,
                    metric_type="roll_forward",
                    score_bin=score_bin,
                    score_midpoint=midpoint,
                    count_total=count_total,
                    count_event_real=count_roll_fwd,
                    sum_predicted_score=float(sum_predicted_score),
                ))
                rows.append(FactPerformanceOutcomes(
                    model_registry_id=reg_id,
                    date_score_key=date_score_key,
                    date_outcome_key=date_outcome_key,
                    metric_type="payment_rate_50",
                    score_bin=score_bin,
                    score_midpoint=midpoint,
                    count_total=count_total,
                    count_event_real=count_payment,
                    sum_predicted_score=float(sum_predicted_score),
                ))

        self.session.add_all(rows)
        self.session.flush()
        return len(rows)

    def _get_event_rate(self, seg_id: str, bin_idx: int, week: int) -> float:
        """
        Tasa de evento por bin (incumplimiento). Score bajo (bin 0) = alto riesgo.
        s1: Gini cae gradualmente (distribuciones menos separadas).
        """
        # Base: probabilidad decrece conforme sube el score
        base_high_risk = 0.80  # bin 0 (score 0-100)
        base_low_risk = 0.05   # bin 9 (score 900-1000)

        if seg_id == "s1":
            # Gini cae de 0.45 → 0.28: las distribuciones se acercan
            gini_factor = 1 - (week - 1) * 0.021  # gradual decay
            base_high_risk = 0.80 * gini_factor + 0.50 * (1 - gini_factor)
            base_low_risk = 0.05 * gini_factor + 0.30 * (1 - gini_factor)

        rate = base_high_risk - (base_high_risk - base_low_risk) * (bin_idx / 9.0)
        noise = self.rng.gauss(0, 0.02)
        return max(0.01, min(0.99, rate + noise))

    def _get_roll_forward(self, seg_id: str, bin_idx: int, week: int) -> float:
        """
        Tasa de deterioro (debe DECRECER conforme sube el score).
        s4: ordering violation en semanas 7-8 (bins 3 y 4 invertidos).
        """
        base = 0.65 - bin_idx * 0.06
        base = max(0.03, min(0.85, base))

        # s4: inversión entre bins 3 y 4 en semanas 7-8
        if seg_id == "s4" and week >= 7:
            if bin_idx == 3:
                base = 0.65 - 4 * 0.06  # valor del bin 4 (más bajo)
            elif bin_idx == 4:
                base = 0.65 - 3 * 0.06  # valor del bin 3 (más alto)

        noise = self.rng.gauss(0, 0.01)
        return max(0.01, min(0.99, base + noise))

    def _get_payment_rate(self, seg_id: str, bin_idx: int, week: int) -> float:
        """PaymentRate: debe CRECER conforme sube el score."""
        base = 0.15 + bin_idx * 0.08
        base = max(0.05, min(0.95, base))
        noise = self.rng.gauss(0, 0.01)
        return max(0.01, min(0.99, base + noise))
