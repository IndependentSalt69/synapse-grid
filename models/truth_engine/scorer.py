"""
models/truth_engine/scorer.py

Inference pipeline for the Truth Engine:
1. Load LightGBM model
2. Score each meter candidate
3. Apply 90% confidence gate + repeat-pattern gate
4. Write qualifying alerts to alert_events table (NEW/WATCHING)
5. Write sub-threshold records to shadow_events table

Routing logic (from structure.md):
- score >= 0.90 AND (consecutive_days >= 2 OR repeat_days >= 3) → alert_events, state=NEW
- score >= 0.90 AND neither condition → alert_events, state=WATCHING
- score < 0.90 → shadow_events only
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from models.truth_engine.train import TRUTH_ENGINE_FEATURES

CONFIDENCE_THRESHOLD = 0.90
DB_PATH_DEFAULT = "data/synapse_grid.db"

CREATE_ALERT_TABLE = """
CREATE TABLE IF NOT EXISTS alert_events (
    alert_id                    TEXT PRIMARY KEY,
    meter_id                    TEXT NOT NULL,
    alert_type                  TEXT NOT NULL,
    state                       TEXT NOT NULL DEFAULT 'NEW',
    anomaly_confidence          REAL NOT NULL,
    pattern_type                TEXT,
    triggered_at                TEXT NOT NULL,
    pct_deviation_from_baseline REAL,
    pct_deviation_from_peer_median REAL,
    pct_deviation_from_cluster_norm REAL,
    z_score                     REAL,
    shap_top3                   TEXT,
    peer_status_summary         TEXT,
    repeat_days_count           INTEGER DEFAULT 0,
    dispatch_action             TEXT,
    dismiss_reason              TEXT,
    resolved_at                 TEXT,
    resolver_id                 TEXT,
    feeder_id                   TEXT
)
"""

CREATE_SHADOW_TABLE = """
CREATE TABLE IF NOT EXISTS shadow_events (
    alert_id                    TEXT PRIMARY KEY,
    meter_id                    TEXT NOT NULL,
    alert_type                  TEXT NOT NULL,
    state                       TEXT NOT NULL DEFAULT 'NEW',
    anomaly_confidence          REAL NOT NULL,
    pattern_type                TEXT,
    triggered_at                TEXT NOT NULL,
    pct_deviation_from_baseline REAL,
    pct_deviation_from_peer_median REAL,
    pct_deviation_from_cluster_norm REAL,
    z_score                     REAL,
    shap_top3                   TEXT,
    peer_status_summary         TEXT,
    repeat_days_count           INTEGER DEFAULT 0,
    dispatch_action             TEXT,
    dismiss_reason              TEXT,
    resolved_at                 TEXT,
    resolver_id                 TEXT,
    feeder_id                   TEXT
)
"""


def _get_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(CREATE_ALERT_TABLE)
    conn.execute(CREATE_SHADOW_TABLE)
    conn.commit()
    return conn


def count_anomaly_days_in_last_5(meter_id: str, conn: sqlite3.Connection) -> int:
    """Count distinct days in last 5 calendar days where meter had an alert."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    row = conn.execute(
        "SELECT COUNT(DISTINCT DATE(triggered_at)) FROM alert_events "
        "WHERE meter_id = ? AND triggered_at >= ?",
        (meter_id, cutoff),
    ).fetchone()
    return int(row[0]) if row else 0


def count_consecutive_anomaly_days(meter_id: str, conn: sqlite3.Connection) -> int:
    """Count the current streak of consecutive days with alerts for this meter."""
    rows = conn.execute(
        "SELECT DISTINCT DATE(triggered_at) FROM alert_events "
        "WHERE meter_id = ? ORDER BY triggered_at DESC LIMIT 10",
        (meter_id,),
    ).fetchall()
    if not rows:
        return 0
    dates = []
    for r in rows:
        try:
            dates.append(datetime.strptime(r[0], "%Y-%m-%d").date())
        except (ValueError, TypeError):
            continue
    if not dates:
        return 0
    streak = 1
    for j in range(1, len(dates)):
        if (dates[j - 1] - dates[j]).days == 1:
            streak += 1
        else:
            break
    return streak


def _determine_pattern_type(row: pd.Series) -> str:
    """Determine pattern_type from feature values."""
    if row.get("is_recurring_daily_pattern", False):
        return "RECURRING_DAILY_DIP"
    if row.get("is_sustained_multiday_drop", False):
        return "SUSTAINED_DROP"
    if row.get("pct_deviation_from_baseline", 0) > 50:
        return "SPIKE"
    return "SUSTAINED_DROP"


def _determine_alert_type(row: pd.Series) -> str:
    """Determine alert_type from feature values."""
    if row.get("pct_deviation_from_baseline", 0) > 50:
        return "LOAD_STRESS"
    return "THEFT_SUSPECT"


def build_alert_object(row: pd.Series, score: float, repeat_days: int) -> dict:
    """Build a canonical alert object from a feature row."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "alert_id": str(uuid.uuid4()),
        "meter_id": str(row.get("meter_id", "")),
        "alert_type": _determine_alert_type(row),
        "state": "NEW",
        "anomaly_confidence": float(score),
        "pattern_type": _determine_pattern_type(row),
        "triggered_at": now,
        "pct_deviation_from_baseline": _safe_float(row.get("pct_deviation_from_baseline")),
        "pct_deviation_from_peer_median": _safe_float(row.get("pct_deviation_from_peer_median")),
        "pct_deviation_from_cluster_norm": _safe_float(row.get("pct_deviation_from_cluster_norm")),
        "z_score": _safe_float(row.get("z_score")),
        "shap_top3": "[]",
        "peer_status_summary": json.dumps({"normal": 0, "elevated": 0, "anomalous": 0}),
        "repeat_days_count": repeat_days,
        "dispatch_action": None,
        "dismiss_reason": None,
        "resolved_at": None,
        "resolver_id": None,
        "feeder_id": str(row.get("feeder_id", "")),
    }


def _safe_float(val: Any) -> float | None:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _write_alert(alert: dict, table: str, conn: sqlite3.Connection) -> None:
    """Insert or update an alert record."""
    # Check for existing alert for same (meter_id, date)
    alert_date = alert["triggered_at"][:10]
    existing = conn.execute(
        f"SELECT alert_id FROM {table} WHERE meter_id = ? AND DATE(triggered_at) = ?",
        (alert["meter_id"], alert_date),
    ).fetchone()

    if existing:
        # Update existing record
        conn.execute(
            f"UPDATE {table} SET anomaly_confidence=?, state=?, repeat_days_count=? "
            f"WHERE alert_id=?",
            (alert["anomaly_confidence"], alert["state"], alert["repeat_days_count"], existing[0]),
        )
    else:
        conn.execute(
            f"INSERT INTO {table} "
            "(alert_id, meter_id, alert_type, state, anomaly_confidence, pattern_type, "
            "triggered_at, pct_deviation_from_baseline, pct_deviation_from_peer_median, "
            "pct_deviation_from_cluster_norm, z_score, shap_top3, peer_status_summary, "
            "repeat_days_count, dispatch_action, dismiss_reason, resolved_at, resolver_id, feeder_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                alert["alert_id"], alert["meter_id"], alert["alert_type"], alert["state"],
                alert["anomaly_confidence"], alert["pattern_type"], alert["triggered_at"],
                alert["pct_deviation_from_baseline"], alert["pct_deviation_from_peer_median"],
                alert["pct_deviation_from_cluster_norm"], alert["z_score"],
                alert["shap_top3"], alert["peer_status_summary"], alert["repeat_days_count"],
                alert["dispatch_action"], alert["dismiss_reason"], alert["resolved_at"],
                alert["resolver_id"], alert["feeder_id"],
            ),
        )
    conn.commit()


def score_and_gate(
    feature_matrix: pd.DataFrame,
    lgbm_model: Any,
    db_path: str = DB_PATH_DEFAULT,
) -> dict:
    """
    Score all meters and route alerts to the correct queue.

    Parameters
    ----------
    feature_matrix : pd.DataFrame
        Full feature matrix.
    lgbm_model : LGBMClassifier
        Trained Truth Engine model.
    db_path : str
        Path to the SQLite database.

    Returns
    -------
    dict
        Summary: {alerts_new, alerts_watching, shadow_records}
    """
    conn = _get_db(db_path)

    # Get one row per meter: the most recent reading
    feature_matrix = feature_matrix.copy()
    feature_matrix["timestamp"] = pd.to_datetime(feature_matrix["timestamp"], utc=True)
    candidates = (
        feature_matrix.sort_values("timestamp")
        .groupby("meter_id", sort=False)
        .last()
        .reset_index()
    )

    available_features = [f for f in TRUTH_ENGINE_FEATURES if f in candidates.columns]
    X = candidates[available_features].copy()

    # Cast bool columns
    for col in ("is_sustained_multiday_drop", "is_recurring_daily_pattern"):
        if col in X.columns:
            X[col] = X[col].astype("int8")

    # Fill NaN
    X = X.fillna(X.median(numeric_only=True))

    # Score
    scores = lgbm_model.predict_proba(X)[:, 1]

    alerts_new = 0
    alerts_watching = 0
    shadow_records = 0

    for i, (_, row) in enumerate(candidates.iterrows()):
        meter_id = str(row["meter_id"])
        score = float(scores[i])

        repeat_days = count_anomaly_days_in_last_5(meter_id, conn)
        consecutive_days = count_consecutive_anomaly_days(meter_id, conn)

        alert = build_alert_object(row, score, repeat_days)

        if score >= CONFIDENCE_THRESHOLD:
            if consecutive_days >= 2 or repeat_days >= 3:
                alert["state"] = "NEW"
                _write_alert(alert, "alert_events", conn)
                alerts_new += 1
            else:
                alert["state"] = "WATCHING"
                _write_alert(alert, "alert_events", conn)
                alerts_watching += 1
        else:
            _write_alert(alert, "shadow_events", conn)
            shadow_records += 1

    conn.close()
    return {
        "alerts_new": alerts_new,
        "alerts_watching": alerts_watching,
        "shadow_records": shadow_records,
    }


def get_pipeline_summary(db_path: str = DB_PATH_DEFAULT) -> dict:
    """Return summary counts from the alert and shadow tables."""
    if not Path(db_path).exists():
        return {"meters_processed": 0, "alerts_new": 0, "alerts_watching": 0, "shadow_records": 0}
    conn = sqlite3.connect(db_path)
    try:
        new_count = conn.execute(
            "SELECT COUNT(*) FROM alert_events WHERE state='NEW'"
        ).fetchone()[0]
        watching_count = conn.execute(
            "SELECT COUNT(*) FROM alert_events WHERE state='WATCHING'"
        ).fetchone()[0]
        shadow_count = conn.execute(
            "SELECT COUNT(*) FROM shadow_events"
        ).fetchone()[0]
        meter_count = conn.execute(
            "SELECT COUNT(DISTINCT meter_id) FROM alert_events"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        new_count = watching_count = shadow_count = meter_count = 0
    finally:
        conn.close()
    return {
        "meters_processed": int(meter_count),
        "alerts_new": int(new_count),
        "alerts_watching": int(watching_count),
        "shadow_records": int(shadow_count),
    }
