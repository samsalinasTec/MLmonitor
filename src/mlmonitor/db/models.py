"""
SQLAlchemy ORM — 7 tablas del modelo de datos MLMonitor.

Reglas:
- META tables: SCD2 con valid_from/valid_to. Nunca se sobreescriben.
- FACT tables: solo append. PK compuesto evita duplicados.
"""

import json
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    ForeignKey,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator


class JSONText(TypeDecorator):
    """JSON stored as TEXT — compatible con SQLite y Oracle."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            return json.dumps(value)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return json.loads(value)
        return value


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# META tables
# ---------------------------------------------------------------------------


class MetaModelRegistry(Base):
    """Registro maestro de modelos. SCD2."""

    __tablename__ = "META_MODEL_REGISTRY"
    __table_args__ = (
        UniqueConstraint(
            "model_id", "submodel_id", "valid_from",
            name="uq_meta_model_registry"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(String(100), nullable=False, index=True)
    submodel_id = Column(String(20), nullable=False)
    model_name = Column(String(200), nullable=False)
    model_description = Column(String(200))
    model_type = Column(String(50), nullable=False)  # scorecard, logistic_regression, xgboost, etc.
    target_definition = Column(String(500))  # qué predice el modelo en lenguaje natural
    score_min = Column(Integer, default=0)
    score_max = Column(Integer, default=1000)
    lag_semanas = Column(Integer, default=8)
    feature_count = Column(Integer)
    training_cutoff_date = Column(Date)
    owner_team = Column(String(100))
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)  # NULL = vigente
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class MetaVariables(Base):
    """Catálogo de variables por modelo. SCD2."""

    __tablename__ = "META_VARIABLES"
    __table_args__ = (
        UniqueConstraint(
            "model_registry_id", "variable_name", "valid_from",
            name="uq_meta_variables"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_registry_id = Column(Integer, ForeignKey("META_MODEL_REGISTRY.id"), nullable=False, index=True)
    variable_name = Column(String(100), nullable=False)
    variable_type = Column(String(20), nullable=False)  # numeric | categorical
    variable_rol = Column(String(20), nullable=True, default="input")  # input | output | target
    lag_semanas = Column(Integer, nullable=True)        # ventana de observación; solo variable_rol="target"
    ascending_order = Column(Boolean, nullable=True)    # True=crece con score (payment), False=decrece (b_malo); solo targets
    description = Column(String(300))
    woe_categories = Column(JSONText)  # para categóricas: lista de valores
    binning_rules = Column(JSONText)  # ej: {"type": "fixed_cuts", "cuts": [0, 201, ...]}
    source_table = Column(String(200))  # tabla física origen, ej: MA_B.tbl_comportamiento_pagos
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)


class MetaMetricThresholds(Base):
    """Catalogo de metricas y umbrales de alerta. model_registry_id NULL = global. SCD2."""

    __tablename__ = "META_METRIC_THRESHOLDS"
    __table_args__ = (
        UniqueConstraint(
            "model_registry_id", "metric_name", "valid_from",
            name="uq_meta_metric_thresholds"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    metric_name = Column(String(100), nullable=False)
    model_registry_id = Column(Integer, ForeignKey("META_MODEL_REGISTRY.id"), nullable=True, index=True)  # NULL = global
    warning_threshold = Column(Float)
    critical_threshold = Column(Float)
    direction = Column(String(20), default="higher_worse")  # higher_worse | lower_worse
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)


class MetaBaselineDistributions(Base):
    """Distribuciones de referencia del baseline de entrenamiento.

    Separada de FACT_DISTRIBUTIONS porque el baseline no es una semana de
    produccion: es un artefacto de entrenamiento con formato y ciclo de vida
    distintos.  Se pobla una sola vez desde bootstrap.py.

    bin_percentage es derivado (bin_count / total_records) y se guarda
    redundante para evitar el computo en cada query de PSI.  Ambos campos
    se calculan juntos al insertar y nunca se actualizan por separado.
    """

    __tablename__ = "META_BASELINE_DISTRIBUTIONS"
    __table_args__ = (
        UniqueConstraint(
            "model_registry_id", "variable_id", "bin_label",
            name="uq_meta_baseline_distributions",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_registry_id = Column(Integer, ForeignKey("META_MODEL_REGISTRY.id"), nullable=False, index=True)
    variable_id = Column(Integer, ForeignKey("META_VARIABLES.id"), nullable=False, index=True)
    bin_label = Column(String(100), nullable=False)
    bin_count = Column(Integer, default=0)
    bin_percentage = Column(Float)
    null_count = Column(Integer, default=0)
    total_records = Column(Integer)
    loaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# FACT tables
# ---------------------------------------------------------------------------


class FactDistributions(Base):
    """Distribuciones semanales de produccion por segmento y semana. Solo append.

    Solo contiene datos de produccion (semanas incrementales).
    La referencia de entrenamiento vive en META_BASELINE_DISTRIBUTIONS.
    """

    __tablename__ = "FACT_DISTRIBUTIONS"
    __table_args__ = (
        UniqueConstraint(
            "model_registry_id", "variable_id", "origination_week", "bin_label",
            name="uq_fact_distributions"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_registry_id = Column(Integer, ForeignKey("META_MODEL_REGISTRY.id"), nullable=False, index=True)
    variable_id = Column(Integer, ForeignKey("META_VARIABLES.id"), nullable=False, index=True)
    origination_week = Column(Date, nullable=False, index=True)
    bin_label = Column(String(100), nullable=False)
    bin_count = Column(Integer, default=0)
    bin_percentage = Column(Float)
    null_count = Column(Integer, default=0)
    sum_value = Column(Float)  # suma de valores en el bin (para media por bin)
    total_records = Column(Integer)
    loaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class FactPerformanceBinned(Base):
    """Outcomes de performance por score bin. Solo append. Conteos atómicos; tasas se calculan al vuelo."""

    __tablename__ = "FACT_PERFORMANCE_BINNED"
    __table_args__ = (
        UniqueConstraint(
            "model_registry_id", "origination_week", "execution_week",
            "metric_type", "score_bin",
            name="uq_fact_performance_binned"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_registry_id = Column(Integer, ForeignKey("META_MODEL_REGISTRY.id"), nullable=False, index=True)
    origination_week = Column(Date, nullable=False, index=True)  # semana de origen del score
    execution_week = Column(Date, nullable=False, index=True)  # semana ISO en que corrió el ETL
    metric_type = Column(String(50), nullable=False)  # b_malo2_4, b_malo4_6, b_malo8_13, b_malo8_16, b_malo14_26, b_malo14_52
    score_bin = Column(String(20), nullable=False)  # "0-100", "100-200", ...
    score_midpoint = Column(Integer)
    count_total = Column(Integer, default=0)
    count_event_real = Column(Integer, default=0)  # mora / atraso real
    sum_predicted_score = Column(Float)  # para calibración: score promedio = sum/count_total
    loaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class FactPerformanceIndividual(Base):
    """Outcomes a nivel de crédito individual. Solo append.

    origination_week = semana de surtimiento (disbursement week).
    execution_week   = semana de observación (cuando se evaluó el outcome).
    La madurez se garantiza por el filtro del ETL incremental:
    semana_num = W - lag, por lo que no se necesita campo semanas_vida.
    """

    __tablename__ = "FACT_PERFORMANCE_INDIVIDUAL"
    __table_args__ = (
        UniqueConstraint(
            "credito_id", "model_registry_id", "ventana",
            name="uq_fact_perf_individual",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    credito_id = Column(String(50), nullable=False, index=True)
    model_registry_id = Column(Integer, ForeignKey("META_MODEL_REGISTRY.id"), nullable=False, index=True)
    origination_week = Column(Date, nullable=False, index=True)      # semana de surtimiento
    execution_week = Column(Date, nullable=False)                     # semana de observación
    fnpuntaje = Column(Float)                                         # score continuo real
    ventana = Column(String(50), nullable=False)                      # nombre de la variable target
    flag = Column(Integer, nullable=False)                            # 0 o 1, nunca null
    loaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class FactMetricsHistory(Base):
    """Historial de métricas calculadas. Solo append."""

    __tablename__ = "FACT_METRICS_HISTORY"
    __table_args__ = (
        UniqueConstraint(
        "model_registry_id", "calculation_week", "metric_id", "variable_id",
        name="uq_fact_metrics_history"
),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_registry_id = Column(Integer, ForeignKey("META_MODEL_REGISTRY.id"), nullable=False, index=True)
    variable_id = Column(Integer, ForeignKey("META_VARIABLES.id"), nullable=True)
    calculation_week = Column(Date, nullable=False, index=True)
    metric_id = Column(Integer, ForeignKey("META_METRIC_THRESHOLDS.id"), nullable=False, index=True)
    metric_value = Column(Float)
    alert_label = Column(String(20))  # "OK" | "WARNING" | "CRITICAL"
    details = Column(JSONText)  # detalles adicionales por métrica
    calculated_from = Column(String(50))  # FACT_DISTRIBUTIONS | FACT_PERFORMANCE_OUTCOMES
    calculated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
