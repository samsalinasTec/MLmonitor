"""
SQLAlchemy ORM — 6 tablas del modelo de datos MLMonitor.

Reglas:
- META tables: SCD2 con valid_from/valid_to. Nunca se sobreescriben.
- FACT tables: solo append. PK compuesto evita duplicados.
"""

import json
from datetime import date, datetime, timezone

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    SmallInteger,
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
            "model_id", "fleet_id", "valid_from",
            name="uq_meta_model_registry"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(String(100), nullable=False, index=True)
    fleet_id = Column(String(20), nullable=False)
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


# ---------------------------------------------------------------------------
# FACT tables
# ---------------------------------------------------------------------------


class FactDistributions(Base):
    """Distribuciones de variables por segmento y semana. Solo append."""

    __tablename__ = "FACT_DISTRIBUTIONS"
    __table_args__ = (
        UniqueConstraint(
            "model_registry_id", "variable_id", "reference_week", "bin_label",
            name="uq_fact_distributions"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_registry_id = Column(Integer, ForeignKey("META_MODEL_REGISTRY.id"), nullable=False, index=True)
    variable_id = Column(Integer, ForeignKey("META_VARIABLES.id"), nullable=False, index=True)
    reference_week = Column(Date, nullable=False, index=True)
    reference_flag = Column(SmallInteger, default=0)  # 1 = baseline de entrenamiento
    bin_label = Column(String(100), nullable=False)
    bin_count = Column(Integer, default=0)
    bin_percentage = Column(Float)
    null_count = Column(Integer, default=0)
    sum_value = Column(Float)  # suma de valores en el bin (para media por bin)
    total_records = Column(Integer)
    loaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class FactPerformanceOutcomes(Base):
    """Outcomes de performance por score bin. Solo append. Conteos atómicos; tasas se calculan al vuelo."""

    __tablename__ = "FACT_PERFORMANCE_OUTCOMES"
    __table_args__ = (
        UniqueConstraint(
            "model_registry_id", "date_score_key", "date_outcome_key",
            "metric_type", "score_bin",
            name="uq_fact_performance_outcomes"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_registry_id = Column(Integer, ForeignKey("META_MODEL_REGISTRY.id"), nullable=False, index=True)
    date_score_key = Column(Date, nullable=False, index=True)  # semana en que se generó el score
    date_outcome_key = Column(Date, nullable=False, index=True)  # semana en que se observó el outcome (T+lag)
    metric_type = Column(String(50), nullable=False)  # roll_forward, payment_rate_50, b_malo_8_13, etc.
    score_bin = Column(String(20), nullable=False)  # "0-100", "100-200", ...
    score_midpoint = Column(Integer)
    count_total = Column(Integer, default=0)
    count_event_real = Column(Integer, default=0)  # mora / atraso real
    sum_predicted_score = Column(Float)  # para calibración: score promedio = sum/count_total
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
