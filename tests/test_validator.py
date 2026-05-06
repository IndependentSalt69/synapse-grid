"""Tests for pipeline/ingest/validator.py"""

import sqlite3
import pytest
import pandas as pd

from pipeline.ingest.validator import validate_readings


def _make_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["kwh"] = df["kwh"].astype("float64")
    df["voltage"] = df["voltage"].astype("float64")
    df["power_factor"] = df["power_factor"].astype("float64")
    df["reactive_power"] = df["reactive_power"].astype("float64")
    return df


def _get_violations(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT meter_id, violation_type, field_name, observed_value FROM quality_violations"
    ).fetchall()
    conn.close()
    return [
        {"meter_id": r[0], "violation_type": r[1], "field_name": r[2], "observed_value": r[3]}
        for r in rows
    ]


BASE_ROW = {
    "meter_id": "M001",
    "timestamp": "2024-01-01T00:00:00+00:00",
    "kwh": 1.5,
    "voltage": 230.0,
    "power_factor": 0.92,
    "reactive_power": 0.3,
}


class TestValidateReadings:
    def test_returns_original_df_unmodified(self, tmp_path):
        db = str(tmp_path / "qlog.db")
        df = _make_df([BASE_ROW])
        result = validate_readings(df, db_path=db)
        pd.testing.assert_frame_equal(df, result)

    def test_negative_kwh_logged(self, tmp_path):
        db = str(tmp_path / "qlog.db")
        row = {**BASE_ROW, "kwh": -0.5}
        df = _make_df([row])
        validate_readings(df, db_path=db)
        violations = _get_violations(db)
        assert any(v["violation_type"] == "NEGATIVE_KWH" for v in violations)

    def test_negative_kwh_row_not_dropped(self, tmp_path):
        db = str(tmp_path / "qlog.db")
        row = {**BASE_ROW, "kwh": -0.5}
        df = _make_df([row])
        result = validate_readings(df, db_path=db)
        assert len(result) == 1  # row still present

    def test_non_monotonic_timestamp_logged(self, tmp_path):
        db = str(tmp_path / "qlog.db")
        rows = [
            {**BASE_ROW, "timestamp": "2024-01-01T01:00:00+00:00"},
            {**BASE_ROW, "timestamp": "2024-01-01T00:45:00+00:00"},  # goes backward
        ]
        df = _make_df(rows)
        validate_readings(df, db_path=db)
        violations = _get_violations(db)
        assert any(v["violation_type"] == "NON_MONOTONIC_TIMESTAMP" for v in violations)

    def test_voltage_out_of_range_logged_but_row_kept(self, tmp_path):
        db = str(tmp_path / "qlog.db")
        row = {**BASE_ROW, "voltage": 170.0}  # below 180V
        df = _make_df([row])
        result = validate_readings(df, db_path=db)
        violations = _get_violations(db)
        assert any(v["violation_type"] == "VOLTAGE_OUT_OF_RANGE" for v in violations)
        assert len(result) == 1  # row NOT dropped

    def test_voltage_above_260_logged(self, tmp_path):
        db = str(tmp_path / "qlog.db")
        row = {**BASE_ROW, "voltage": 270.0}
        df = _make_df([row])
        validate_readings(df, db_path=db)
        violations = _get_violations(db)
        assert any(v["violation_type"] == "VOLTAGE_OUT_OF_RANGE" for v in violations)

    def test_power_factor_out_of_range_logged(self, tmp_path):
        db = str(tmp_path / "qlog.db")
        row = {**BASE_ROW, "power_factor": 1.5}
        df = _make_df([row])
        validate_readings(df, db_path=db)
        violations = _get_violations(db)
        assert any(v["violation_type"] == "POWER_FACTOR_OUT_OF_RANGE" for v in violations)

    def test_power_factor_negative_logged(self, tmp_path):
        db = str(tmp_path / "qlog.db")
        row = {**BASE_ROW, "power_factor": -0.1}
        df = _make_df([row])
        validate_readings(df, db_path=db)
        violations = _get_violations(db)
        assert any(v["violation_type"] == "POWER_FACTOR_OUT_OF_RANGE" for v in violations)

    def test_all_violations_logged_in_single_pass(self, tmp_path):
        db = str(tmp_path / "qlog.db")
        rows = [
            {**BASE_ROW, "kwh": -1.0, "voltage": 170.0, "power_factor": 1.5},
        ]
        df = _make_df(rows)
        validate_readings(df, db_path=db)
        violations = _get_violations(db)
        types = {v["violation_type"] for v in violations}
        assert "NEGATIVE_KWH" in types
        assert "VOLTAGE_OUT_OF_RANGE" in types
        assert "POWER_FACTOR_OUT_OF_RANGE" in types

    def test_valid_row_produces_no_violations(self, tmp_path):
        db = str(tmp_path / "qlog.db")
        df = _make_df([BASE_ROW])
        validate_readings(df, db_path=db)
        violations = _get_violations(db)
        assert len(violations) == 0

