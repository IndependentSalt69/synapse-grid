"""Tests for pipeline/ingest/meter_reader.py"""

import io
import pytest
import pandas as pd
from pathlib import Path
import tempfile
import os

from pipeline.ingest.meter_reader import load_readings, REQUIRED_COLUMNS


def _make_csv(rows: list[dict], path: str) -> str:
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    return path


VALID_ROW = {
    "meter_id": "M001",
    "timestamp": "2024-01-01T00:00:00+00:00",
    "kwh": 1.5,
    "voltage": 230.0,
    "power_factor": 0.92,
    "reactive_power": 0.3,
}


class TestLoadReadings:
    def test_valid_single_file(self, tmp_path):
        path = str(tmp_path / "readings.csv")
        _make_csv([VALID_ROW], path)
        df = load_readings(path)
        assert len(df) == 1
        assert list(df.columns) == REQUIRED_COLUMNS

    def test_timestamp_is_utc_datetime(self, tmp_path):
        path = str(tmp_path / "readings.csv")
        _make_csv([VALID_ROW], path)
        df = load_readings(path)
        assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])
        assert str(df["timestamp"].dt.tz) == "UTC"

    def test_kwh_is_float64(self, tmp_path):
        path = str(tmp_path / "readings.csv")
        _make_csv([VALID_ROW], path)
        df = load_readings(path)
        assert df["kwh"].dtype == "float64"
        assert df["voltage"].dtype == "float64"
        assert df["power_factor"].dtype == "float64"
        assert df["reactive_power"].dtype == "float64"

    def test_meter_id_is_str(self, tmp_path):
        path = str(tmp_path / "readings.csv")
        _make_csv([VALID_ROW], path)
        df = load_readings(path)
        assert pd.api.types.is_string_dtype(df["meter_id"])

    def test_missing_column_raises_value_error(self, tmp_path):
        path = str(tmp_path / "bad.csv")
        row = {k: v for k, v in VALID_ROW.items() if k != "voltage"}
        _make_csv([row], path)
        with pytest.raises(ValueError, match="voltage"):
            load_readings(path)

    def test_missing_kwh_column_raises(self, tmp_path):
        path = str(tmp_path / "bad.csv")
        row = {k: v for k, v in VALID_ROW.items() if k != "kwh"}
        _make_csv([row], path)
        with pytest.raises(ValueError, match="kwh"):
            load_readings(path)

    def test_multi_file_concat_and_sort(self, tmp_path):
        row1 = {**VALID_ROW, "meter_id": "M002", "timestamp": "2024-01-01T01:00:00+00:00"}
        row2 = {**VALID_ROW, "meter_id": "M001", "timestamp": "2024-01-01T00:00:00+00:00"}
        path1 = str(tmp_path / "f1.csv")
        path2 = str(tmp_path / "f2.csv")
        _make_csv([row1], path1)
        _make_csv([row2], path2)
        df = load_readings([path1, path2])
        assert len(df) == 2
        # Sorted by (meter_id, timestamp): M001 before M002
        assert df.iloc[0]["meter_id"] == "M001"
        assert df.iloc[1]["meter_id"] == "M002"

    def test_file_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            load_readings("nonexistent_file.csv")

    def test_string_path_accepted(self, tmp_path):
        path = str(tmp_path / "readings.csv")
        _make_csv([VALID_ROW], path)
        df = load_readings(path)  # str, not list
        assert len(df) == 1
