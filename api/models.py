"""
api/models.py

SQLAlchemy ORM models for all Synapse-Grid database tables.
All tables use SQLite (dev/demo). Schema created via create_all() on startup.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Column, Float, ForeignKey, Index, Integer, String, Text,
)

from api.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class AlertEvent(Base):
    """
    Actionable alerts (confidence >= 0.90).
    Both NEW and WATCHING state alerts live here.
    """
    __tablename__ = "alert_events"

    alert_id = Column(String, primary_key=True, default=_uuid)
    meter_id = Column(String, nullable=False, index=True)
    alert_type = Column(String, nullable=False)          # THEFT_SUSPECT | LOAD_STRESS | HARDWARE_ISSUE
    state = Column(String, nullable=False, default="NEW") # NEW | UNDER_REVIEW | WATCHING | DISPATCHED | DISMISSED
    anomaly_confidence = Column(Float, nullable=False)
    pattern_type = Column(String)                         # SUSTAINED_DROP | RECURRING_DAILY_DIP | SPIKE
    triggered_at = Column(String, nullable=False)         # ISO8601 UTC
    pct_deviation_from_baseline = Column(Float)
    pct_deviation_from_peer_median = Column(Float)
    pct_deviation_from_cluster_norm = Column(Float)
    z_score = Column(Float)
    shap_top3 = Column(Text)                              # JSON: [{feature, value, plain_english}]
    peer_status_summary = Column(Text)                    # JSON: {normal, elevated, anomalous}
    repeat_days_count = Column(Integer, default=0)
    dispatch_action = Column(String)                      # NULL until resolved
    dismiss_reason = Column(String)                       # NULL unless DISMISSED
    resolved_at = Column(String)                          # ISO8601 UTC, NULL until resolved
    resolver_id = Column(String)                          # NULL until resolved
    feeder_id = Column(String, index=True)                # Denormalized for fast filtering


class ShadowEvent(Base):
    """
    Sub-threshold records (confidence < 0.90).
    Never served to the frontend — used for model improvement only.
    """
    __tablename__ = "shadow_events"

    alert_id = Column(String, primary_key=True, default=_uuid)
    meter_id = Column(String, nullable=False, index=True)
    alert_type = Column(String, nullable=False)
    state = Column(String, nullable=False, default="NEW")
    anomaly_confidence = Column(Float, nullable=False)
    pattern_type = Column(String)
    triggered_at = Column(String, nullable=False)
    pct_deviation_from_baseline = Column(Float)
    pct_deviation_from_peer_median = Column(Float)
    pct_deviation_from_cluster_norm = Column(Float)
    z_score = Column(Float)
    shap_top3 = Column(Text)
    peer_status_summary = Column(Text)
    repeat_days_count = Column(Integer, default=0)
    dispatch_action = Column(String)
    dismiss_reason = Column(String)
    resolved_at = Column(String)
    resolver_id = Column(String)
    feeder_id = Column(String)


class DispatchAuditLog(Base):
    """
    Immutable audit trail of every dispatch action taken by a resolver.
    """
    __tablename__ = "dispatch_audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_id = Column(String, ForeignKey("alert_events.alert_id"), nullable=False)
    action = Column(String, nullable=False)               # DISPATCH_LINEMAN | LOAD_BALANCE | DISMISS
    reason_code = Column(String)                          # NULL unless action=DISMISS
    resolver_id = Column(String, nullable=False)
    resolved_at = Column(String, nullable=False)          # ISO8601 UTC


class MeterReading(Base):
    """
    Per-meter 15-minute interval readings.
    Stores last 14+ days for the API to serve to the frontend chart.
    """
    __tablename__ = "meter_readings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    meter_id = Column(String, nullable=False)
    timestamp = Column(String, nullable=False)            # ISO8601 UTC
    kwh = Column(Float)
    voltage = Column(Float)
    power_factor = Column(Float)
    reactive_power = Column(Float)

    __table_args__ = (
        Index("idx_meter_readings_meter_ts", "meter_id", "timestamp"),
    )


class MeterRegistryCache(Base):
    """
    Cached copy of the meter registry for fast API lookups.
    Populated by run_pipeline.py.
    """
    __tablename__ = "meter_registry_cache"

    meter_id = Column(String, primary_key=True)
    lat = Column(Float)
    lng = Column(Float)
    feeder_id = Column(String, index=True)
    transformer_id = Column(String)
    zone = Column(String)
    consumer_category = Column(String)
    sanctioned_kva = Column(Float)
    connection_date = Column(String)


class FeederStatus(Base):
    """
    Current utilization and 24-hour forecast per feeder.
    Written by inference_runner.py after each pipeline run.
    """
    __tablename__ = "feeder_status"

    feeder_id = Column(String, primary_key=True)
    current_utilization_pct = Column(Float)
    stress_level = Column(String)                         # GREEN | AMBER | RED
    transformer_rated_kva = Column(Float)
    forecast_24h = Column(Text)                           # JSON: [{timestamp, predicted_utilization_pct}]
    updated_at = Column(String)                           # ISO8601 UTC
