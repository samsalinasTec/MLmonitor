"""
SQLAlchemy ORM — 6 tablas del modelo de datos MLMonitor.

Reglas:
- META tables: SCD2 con valid_from/valid_to. Nunca se sobreescriben.
- FACT tables: solo append. PK compuesto evita duplicados.
"""

import json
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
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

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(String(100), nullable=False, index=True)
    model_name = Column(String(200), nullable=False)
    segment_id = Column(String(20), nullable=False)
    segment_description = Column(String(200))
    score_min = Column(Integer, default=0)
    score_max = Column(Integer, default=1000)
    lag_semanas = Column(Integer, default=8)
    feature_count = Column(Integer)
    training_cutoff_date = Column(Date)
    owner_team = Column(String(100))
    is_active = Column(SmallInteger, default=1)
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)  # NULL = vigente
    created_at = Column(DateTime, default=datetime.utcnow)


class MetaVariables(Base):
    """Catálogo de variables por modelo. SCD2."""

    __tablename__ = "META_VARIABLES"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(String(100), nullable=False, index=True)
    segment_id = Column(String(20), nullable=False, index=True)
    variable_name = Column(String(100), nullable=False)
    variable_type = Column(String(20), nullable=False)  # numeric | categorical
    description = Column(String(300))
    woe_categories = Column(JSONText)  # para categóricas: lista de valores
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)


class MetaMetricThresholds(Base):
    """Umbrales de alerta por métrica. model_id_override NULL = global. SCD2."""

    __tablename__ = "META_METRIC_THRESHOLDS"

    id = Column(Integer, primary_key=True, autoincrement=True)
    metric_name = Column(String(100), nullable=False)
    model_id_override = Column(String(100), nullable=True)  # NULL = global
    warning_threshold = Column(Float)
    critical_threshold = Column(Float)
    direction = Column(String(10), default="higher_worse")  # higher_worse | lower_worse
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
            "model_id", "segment_id", "variable_name", "reference_week", "bin_label",
            name="uq_fact_distributions"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(String(100), nullable=False, index=True)
    segment_id = Column(String(20), nullable=False, index=True)
    variable_name = Column(String(100), nullable=False)
    reference_week = Column(Date, nullable=False, index=True)
    reference_flag = Column(SmallInteger, default=0)  # 1 = baseline de entrenamiento
    bin_label = Column(String(100), nullable=False)
    bin_count = Column(Integer, default=0)
    bin_percentage = Column(Float)
    null_count = Column(Integer, default=0)
    total_records = Column(Integer)
    loaded_at = Column(DateTime, default=datetime.utcnow)


class FactPerformanceOutcomes(Base):
    """Outcomes de performance por score bin. Solo append."""

    __tablename__ = "FACT_PERFORMANCE_OUTCOMES"
    __table_args__ = (
        UniqueConstraint(
            "model_id", "segment_id", "reference_week", "score_bin",
            name="uq_fact_performance_outcomes"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(String(100), nullable=False, index=True)
    segment_id = Column(String(20), nullable=False, index=True)
    reference_week = Column(Date, nullable=False, index=True)
    score_bin = Column(String(20), nullable=False)  # "0-100", "100-200", ...
    score_midpoint = Column(Integer)
    count_total = Column(Integer, default=0)
    count_event_real = Column(Integer, default=0)  # mora / atraso real
    roll_forward_rate = Column(Float)  # tasa de deterioro
    payment_rate = Column(Float)  # tasa de pago
    loaded_at = Column(DateTime, default=datetime.utcnow)


class FactMetricsHistory(Base):
    """Historial de métricas calculadas. Solo append."""

    __tablename__ = "FACT_METRICS_HISTORY"
    __table_args__ = (
        UniqueConstraint(
            "model_id", "segment_id", "calculation_week", "metric_name",
            name="uq_fact_metrics_history"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(String(100), nullable=False, index=True)
    segment_id = Column(String(20), nullable=False, index=True)
    calculation_week = Column(Date, nullable=False, index=True)
    metric_name = Column(String(100), nullable=False)
    metric_value = Column(Float)
    alert_flag = Column(SmallInteger, default=0)  # 0=OK, 1=WARNING, 2=CRITICAL
    alert_label = Column(String(20))  # "OK" | "WARNING" | "CRITICAL"
    details = Column(JSONText)  # detalles adicionales por métrica
    calculated_at = Column(DateTime, default=datetime.utcnow)
