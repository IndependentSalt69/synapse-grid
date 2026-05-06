"""
Tests for models/truth_engine/scorer.py — alert confidence gating.

Critical spec requirements (from structure.md):
- score=0.89 → written to shadow_events ONLY, NOT in alert_events
- score=0.91 + repeat_days >= 2 (consecutive) → alert_events with state=NEW
- score=0.91 + repeat_days=0 (no repeat) → alert_events with state=WATCHING
- Alert deduplication: running scorer twice on same data does not create duplicate rows
"""

import sqlite3
import tempfile
import os
import json
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from models.truth_engine.scorer import (
    score_and_gate,
    build_alert_object,
    count_anomaly_days_in_last_5,
    count_consecutive_anomaly_days,
    _write_alert,
    _get_db,
    CONFIDENCE_THRESHOLD,
)


def _make_feature_row(meter_id: str = "M001", feeder_id: str = "F001") -> dict:
    """Create a minimal feature row for testing."""
    return {
        "meter_id": meter_id,
        "feeder_id": feeder_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kwh": 0.5,
        "z_score": -3.5,
        "peer_deviation_score": -85.0,
        "is_sustained_multiday_drop": True,
        "is_recurring_daily_pattern": True,
        "night_activity_score": 0.8,
        "pct_deviation_from_baseline": -82.0,
        "pct_deviation_from_peer_median": -80.0,
        "pct_deviation_from_cluster_norm": -75.0,
        "lag_1h": 0.6,
        "lag_24h": 2.1,
        "lag_48h": 2.0,
        "lag_7d": 2.2,
        "rolling_7d_mean": 2.0,
        "rolling_7d_std": 0.3,
        "trend_slope_3d": -0.5,
        "cluster_id": 0,
    }


def _make_feature_df(meter_id: str = "M001", n_rows: int = 1) -> pd.DataFrame:
    """Create a minimal feature DataFrame for testing."""
    rows = []
    base_ts = datetime.now(timezone.utc)
    for i in range(n_rows):
        row = _make_feature_row(meter_id)
        row["timestamp"] = (base_ts - timedelta(minutes=15 * i)).isoformat()
        rows.append(row)
    return pd.DataFrame(rows)


def _make_mock_model(score: float):
    """Create a mock LightGBM model that always returns the given score."""
    model = MagicMock()
    model.predict_proba.return_value = np.array([[1 - score, score]])
    return model


class TestConfidenceGating:
    def test_score_below_threshold_goes_to_shadow_only(self, tmp_path):
        """score=0.89 → shadow_events only, NOT in alert_events."""
        db_path = str(tmp_path / "test.db")
        model = _make_mock_model(0.89)
        df = _make_feature_df("M001")

        score_and_gate(df, model, db_path=db_path)

        conn = sqlite3.connect(db_path)
        alert_count = conn.execute(
            "SELECT COUNT(*) FROM alert_events WHERE meter_id='M001'"
        ).fetchone()[0]
        shadow_count = conn.execute(
            "SELECT COUNT(*) FROM shadow_events WHERE meter_id='M001'"
        ).fetchone()[0]
        conn.close()

        assert alert_count == 0, "score=0.89 must NOT appear in alert_events"
        assert shadow_count == 1, "score=0.89 must appear in shadow_events"

    def test_score_at_threshold_boundary_goes_to_shadow(self, tmp_path):
        """score=0.8999 (just below 0.90) → shadow only."""
        db_path = str(tmp_path / "test.db")
        model = _make_mock_model(0.8999)
        df = _make_feature_df("M001")

        score_and_gate(df, model, db_path=db_path)

        conn = sqlite3.connect(db_path)
        alert_count = conn.execute(
            "SELECT COUNT(*) FROM alert_events WHERE meter_id='M001'"
        ).fetchone()[0]
        conn.close()
        assert alert_count == 0

    def test_score_above_threshold_no_repeat_goes_to_watching(self, tmp_path):
        """score=0.91 + no prior alerts → alert_events with state=WATCHING."""
        db_path = str(tmp_path / "test.db")
        model = _make_mock_model(0.91)
        df = _make_feature_df("M001")

        score_and_gate(df, model, db_path=db_path)

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT state FROM alert_events WHERE meter_id='M001'"
        ).fetchone()
        conn.close()

        assert row is not None, "score=0.91 must appear in alert_events"
        assert row[0] == "WATCHING", f"Expected WATCHING, got {row[0]}"

    def test_score_above_threshold_with_repeat_goes_to_new(self, tmp_path):
        """score=0.91 + 2 consecutive prior days → alert_events with state=NEW."""
        db_path = str(tmp_path / "test.db")

        # Pre-populate alert_events with 2 consecutive prior days
        conn = _get_db(db_path)
        now = datetime.now(timezone.utc)
        for days_ago in [2, 1]:
            prior_ts = (now - timedelta(days=days_ago)).isoformat()
            conn.execute(
                "INSERT INTO alert_events "
                "(alert_id, meter_id, alert_type, state, anomaly_confidence, "
                "triggered_at, feeder_id) VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), "M001", "THEFT_SUSPECT", "WATCHING",
                 0.91, prior_ts, "F001"),
            )
        conn.commit()
        conn.close()

        model = _make_mock_model(0.91)
        df = _make_feature_df("M001")

        score_and_gate(df, model, db_path=db_path)

        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT state FROM alert_events WHERE meter_id='M001' ORDER BY triggered_at DESC"
        ).fetchall()
        conn.close()

        # The most recent alert should be NEW
        assert rows[0][0] == "NEW", f"Expected NEW, got {rows[0][0]}"

    def test_score_091_with_3_of_5_days_goes_to_new(self, tmp_path):
        """score=0.91 + 3 of last 5 days with alerts → state=NEW."""
        db_path = str(tmp_path / "test.db")

        conn = _get_db(db_path)
        now = datetime.now(timezone.utc)
        # 3 alerts in last 5 days (non-consecutive)
        for days_ago in [4, 2, 1]:
            prior_ts = (now - timedelta(days=days_ago)).isoformat()
            conn.execute(
                "INSERT INTO alert_events "
                "(alert_id, meter_id, alert_type, state, anomaly_confidence, "
                "triggered_at, feeder_id) VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), "M001", "THEFT_SUSPECT", "WATCHING",
                 0.91, prior_ts, "F001"),
            )
        conn.commit()
        conn.close()

        model = _make_mock_model(0.91)
        df = _make_feature_df("M001")

        score_and_gate(df, model, db_path=db_path)

        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT state FROM alert_events WHERE meter_id='M001' ORDER BY triggered_at DESC"
        ).fetchall()
        conn.close()

        assert rows[0][0] == "NEW"

    def test_alert_schema_complete_for_new_alert(self, tmp_path):
        """Alert written to alert_events must have all canonical schema fields."""
        db_path = str(tmp_path / "test.db")

        # Pre-populate for NEW state
        conn = _get_db(db_path)
        now = datetime.now(timezone.utc)
        for days_ago in [2, 1]:
            prior_ts = (now - timedelta(days=days_ago)).isoformat()
            conn.execute(
                "INSERT INTO alert_events "
                "(alert_id, meter_id, alert_type, state, anomaly_confidence, "
                "triggered_at, feeder_id) VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), "M001", "THEFT_SUSPECT", "WATCHING",
                 0.91, prior_ts, "F001"),
            )
        conn.commit()
        conn.close()

        model = _make_mock_model(0.91)
        df = _make_feature_df("M001")
        score_and_gate(df, model, db_path=db_path)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM alert_events WHERE meter_id='M001' ORDER BY triggered_at DESC LIMIT 1"
        ).fetchone()
        conn.close()

        assert row is not None
        required_fields = [
            "alert_id", "meter_id", "alert_type", "state", "anomaly_confidence",
            "triggered_at", "feeder_id",
        ]
        for field in required_fields:
            assert row[field] is not None, f"Field '{field}' is None in alert schema"

        assert row["state"] == "NEW"
        assert row["anomaly_confidence"] >= CONFIDENCE_THRESHOLD


class TestAlertDeduplication:
    def test_running_scorer_twice_does_not_duplicate(self, tmp_path):
        """Running score_and_gate twice on same data must not create duplicate rows."""
        db_path = str(tmp_path / "test.db")
        model = _make_mock_model(0.91)
        df = _make_feature_df("M001")

        score_and_gate(df, model, db_path=db_path)
        score_and_gate(df, model, db_path=db_path)

        conn = sqlite3.connect(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM alert_events WHERE meter_id='M001'"
        ).fetchone()[0]
        conn.close()

        assert count == 1, f"Expected 1 alert, got {count} (deduplication failed)"


class TestRepeatDayCounting:
    def test_count_anomaly_days_in_last_5_empty(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = _get_db(db_path)
        count = count_anomaly_days_in_last_5("M001", conn)
        conn.close()
        assert count == 0

    def test_count_consecutive_days_empty(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = _get_db(db_path)
        count = count_consecutive_anomaly_days("M001", conn)
        conn.close()
        assert count == 0

    def test_count_consecutive_days_streak_of_2(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = _get_db(db_path)
        now = datetime.now(timezone.utc)
        for days_ago in [1, 0]:
            ts = (now - timedelta(days=days_ago)).isoformat()
            conn.execute(
                "INSERT INTO alert_events "
                "(alert_id, meter_id, alert_type, state, anomaly_confidence, "
                "triggered_at, feeder_id) VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), "M001", "THEFT_SUSPECT", "WATCHING", 0.91, ts, "F001"),
            )
        conn.commit()
        count = count_consecutive_anomaly_days("M001", conn)
        conn.close()
        assert count >= 2
