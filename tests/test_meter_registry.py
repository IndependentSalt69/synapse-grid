"""Tests for pipeline/ingest/meter_registry.py"""

import pytest
import pandas as pd
from datetime import date

from pipeline.ingest.meter_registry import load_registry, get_meter, REQUIRED_COLUMNS


VALID_ROW = {
    "meter_id": "M001",
    "lat": 12.9716,
    "lng": 77.5946,
    "feeder_id": "F001",
    "transformer_id": "T001",
    "zone": "ZONE_1",
    "consumer_category": "RESIDENTIAL",
    "sanctioned_kva": 50.0,
    "connection_date": "2020-01-15",
}


def _make_registry_csv(rows: list[dict], path: str) -> str:
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


class TestLoadRegistry:
    def test_valid_file_returns_df_and_dict(self, tmp_path):
        path = str(tmp_path / "registry.csv")
        _make_registry_csv([VALID_ROW], path)
        df, registry = load_registry(path)
        assert isinstance(df, pd.DataFrame)
        assert isinstance(registry, dict)
        assert len(df) == 1
        assert "M001" in registry

    def test_missing_column_raises_value_error(self, tmp_path):
        path = str(tmp_path / "bad.csv")
        row = {k: v for k, v in VALID_ROW.items() if k != "feeder_id"}
        _make_registry_csv([row], path)
        with pytest.raises(ValueError, match="feeder_id"):
            load_registry(path)

    def test_all_required_columns_validated(self, tmp_path):
        for missing_col in REQUIRED_COLUMNS:
            path = str(tmp_path / f"bad_{missing_col}.csv")
            row = {k: v for k, v in VALID_ROW.items() if k != missing_col}
            _make_registry_csv([row], path)
            with pytest.raises(ValueError, match=missing_col):
                load_registry(path)

    def test_sanctioned_kva_is_float64(self, tmp_path):
        path = str(tmp_path / "registry.csv")
        _make_registry_csv([VALID_ROW], path)
        df, _ = load_registry(path)
        assert df["sanctioned_kva"].dtype == "float64"

    def test_connection_date_is_date(self, tmp_path):
        path = str(tmp_path / "registry.csv")
        _make_registry_csv([VALID_ROW], path)
        df, _ = load_registry(path)
        assert isinstance(df["connection_date"].iloc[0], date)

    def test_lat_lng_are_float64(self, tmp_path):
        path = str(tmp_path / "registry.csv")
        _make_registry_csv([VALID_ROW], path)
        df, _ = load_registry(path)
        assert df["lat"].dtype == "float64"
        assert df["lng"].dtype == "float64"

    def test_non_positive_kva_raises(self, tmp_path):
        path = str(tmp_path / "bad_kva.csv")
        row = {**VALID_ROW, "sanctioned_kva": 0.0}
        _make_registry_csv([row], path)
        with pytest.raises(ValueError, match="sanctioned_kva"):
            load_registry(path)

    def test_o1_lookup_returns_correct_record(self, tmp_path):
        path = str(tmp_path / "registry.csv")
        row2 = {**VALID_ROW, "meter_id": "M002", "feeder_id": "F002"}
        _make_registry_csv([VALID_ROW, row2], path)
        _, registry = load_registry(path)
        record = get_meter(registry, "M002")
        assert record["feeder_id"] == "F002"

    def test_lookup_missing_meter_raises_key_error(self, tmp_path):
        path = str(tmp_path / "registry.csv")
        _make_registry_csv([VALID_ROW], path)
        _, registry = load_registry(path)
        with pytest.raises(KeyError):
            get_meter(registry, "NONEXISTENT")

    def test_file_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            load_registry("nonexistent_registry.csv")
