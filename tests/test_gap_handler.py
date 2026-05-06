"""Tests for pipeline/impute/gap_handler.py

Critical test cases per spec:
- Short gap (1-3 slots) → imputed with 7-day same-slot median
- Extended gap (>3 slots) → hardware_issue_flags.csv written, slots remain NaN
- No gap → DataFrame unchanged
"""

import csv
import pytest
import numpy as np
import pandas as pd
from pathlib import Path

from pipeline.impute.gap_handler import handle_gaps


def _make_complete_series(
    meter_id: str = "M001",
    n_days: int = 14,
    base_kwh: float = 2.0,
    start: str = "2024-01-01",
) -> pd.DataFrame:
    """Create a complete 15-min series with no gaps."""
    timestamps = pd.date_range(start=start, periods=n_days * 96, freq="15min", tz="UTC")
    return pd.DataFrame({
        "meter_id": meter_id,
        "timestamp": timestamps,
        "kwh": base_kwh,
        "voltage": 230.0,
        "power_factor": 0.92,
        "reactive_power": 0.3,
    })


class TestHandleGaps:
    def test_no_gap_unchanged(self, tmp_path):
        """A series with no NaN values must be returned unchanged."""
        flags = str(tmp_path / "flags.csv")
        df = _make_complete_series()
        result = handle_gaps(df, flags_path=flags)
        # All kwh values should still be 2.0
        assert result["kwh"].isna().sum() == 0
        assert (result["kwh"].dropna() == 2.0).all()

    def test_no_gap_flags_file_written_with_header_only(self, tmp_path):
        """Even with no gaps, hardware_issue_flags.csv must be written with header."""
        flags = str(tmp_path / "flags.csv")
        df = _make_complete_series()
        handle_gaps(df, flags_path=flags)
        assert Path(flags).exists()
        with open(flags) as f:
            reader = csv.reader(f)
            header = next(reader)
            assert header == ["meter_id", "gap_start", "gap_end", "gap_length_slots"]
            rows = list(reader)
            assert len(rows) == 0  # no flags

    def test_short_gap_1_slot_imputed(self, tmp_path):
        """A single missing slot should be filled with the 7-day same-slot median."""
        flags = str(tmp_path / "flags.csv")
        df = _make_complete_series(n_days=14, base_kwh=3.0)
        # Introduce 1 NaN at day 8, slot 0 (00:00)
        gap_idx = df[df["timestamp"] == "2024-01-09 00:00:00+00:00"].index
        assert len(gap_idx) == 1
        df.loc[gap_idx, "kwh"] = np.nan

        result = handle_gaps(df, flags_path=flags)
        # The slot should be filled (not NaN)
        filled = result[result["timestamp"] == "2024-01-09 00:00:00+00:00"]["kwh"]
        assert not filled.isna().any()
        # Value should be close to 3.0 (the median of same-slot history)
        assert abs(filled.iloc[0] - 3.0) < 0.01

    def test_short_gap_3_slots_imputed(self, tmp_path):
        """Three consecutive missing slots should all be filled."""
        flags = str(tmp_path / "flags.csv")
        df = _make_complete_series(n_days=14, base_kwh=2.5)
        # Introduce 3 consecutive NaN slots
        gap_start = "2024-01-09 04:00:00+00:00"
        gap_end = "2024-01-09 04:30:00+00:00"
        gap_mask = (df["timestamp"] >= gap_start) & (df["timestamp"] <= gap_end)
        df.loc[gap_mask, "kwh"] = np.nan
        assert gap_mask.sum() == 3

        result = handle_gaps(df, flags_path=flags)
        filled = result[(result["timestamp"] >= gap_start) & (result["timestamp"] <= gap_end)]["kwh"]
        assert not filled.isna().any()

    def test_short_gap_no_hardware_flag_written(self, tmp_path):
        """Short gaps must NOT produce a hardware_issue_flags.csv entry."""
        flags = str(tmp_path / "flags.csv")
        df = _make_complete_series(n_days=14, base_kwh=2.0)
        gap_idx = df[df["timestamp"] == "2024-01-09 00:00:00+00:00"].index
        df.loc[gap_idx, "kwh"] = np.nan

        handle_gaps(df, flags_path=flags)
        with open(flags) as f:
            reader = csv.reader(f)
            next(reader)  # skip header
            rows = list(reader)
        assert len(rows) == 0

    def test_extended_gap_4_slots_not_imputed(self, tmp_path):
        """Four consecutive NaN slots must remain NaN (extended gap)."""
        flags = str(tmp_path / "flags.csv")
        df = _make_complete_series(n_days=14, base_kwh=2.0)
        gap_start = "2024-01-09 06:00:00+00:00"
        gap_end = "2024-01-09 06:45:00+00:00"
        gap_mask = (df["timestamp"] >= gap_start) & (df["timestamp"] <= gap_end)
        df.loc[gap_mask, "kwh"] = np.nan
        assert gap_mask.sum() == 4

        result = handle_gaps(df, flags_path=flags)
        still_nan = result[(result["timestamp"] >= gap_start) & (result["timestamp"] <= gap_end)]["kwh"]
        assert still_nan.isna().all()

    def test_extended_gap_hardware_flag_written(self, tmp_path):
        """Extended gap must produce a record in hardware_issue_flags.csv."""
        flags = str(tmp_path / "flags.csv")
        df = _make_complete_series(n_days=14, base_kwh=2.0)
        gap_start = "2024-01-09 06:00:00+00:00"
        gap_end = "2024-01-09 06:45:00+00:00"
        gap_mask = (df["timestamp"] >= gap_start) & (df["timestamp"] <= gap_end)
        df.loc[gap_mask, "kwh"] = np.nan

        handle_gaps(df, flags_path=flags)
        with open(flags) as f:
            reader = csv.reader(f)
            next(reader)  # skip header
            rows = list(reader)
        assert len(rows) == 1
        assert rows[0][0] == "M001"
        assert int(rows[0][3]) == 4  # gap_length_slots

    def test_extended_gap_8_slots_flagged(self, tmp_path):
        """Eight consecutive NaN slots must be flagged with correct length."""
        flags = str(tmp_path / "flags.csv")
        df = _make_complete_series(n_days=14, base_kwh=2.0)
        gap_start_idx = 200
        gap_indices = df.index[gap_start_idx: gap_start_idx + 8]
        df.loc[gap_indices, "kwh"] = np.nan

        handle_gaps(df, flags_path=flags)
        with open(flags) as f:
            reader = csv.reader(f)
            next(reader)
            rows = list(reader)
        assert len(rows) == 1
        assert int(rows[0][3]) == 8

    def test_idempotent(self, tmp_path):
        """Running handle_gaps twice on the same input produces identical output."""
        flags1 = str(tmp_path / "flags1.csv")
        flags2 = str(tmp_path / "flags2.csv")
        df = _make_complete_series(n_days=14, base_kwh=2.0)
        gap_idx = df.index[100:103]
        df.loc[gap_idx, "kwh"] = np.nan

        result1 = handle_gaps(df.copy(), flags_path=flags1)
        result2 = handle_gaps(df.copy(), flags_path=flags2)
        pd.testing.assert_frame_equal(
            result1.reset_index(drop=True),
            result2.reset_index(drop=True),
        )
