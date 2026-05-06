"""
pipeline/impute/gap_handler.py

Handle missing meter readings:
- Short gaps (1–3 consecutive NaN slots) → impute with 7-day same-slot rolling median
- Extended gaps (>3 consecutive NaN slots) → flag as HARDWARE_ISSUE, do NOT impute

Writes hardware issue flags to data/processed/hardware_issue_flags.csv.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

import numpy as np
import pandas as pd

FLAGS_PATH_DEFAULT = "data/processed/hardware_issue_flags.csv"
FLAGS_COLUMNS = ["meter_id", "gap_start", "gap_end", "gap_length_slots"]


def _run_length_encode(series: pd.Series) -> list[tuple[int, int, bool]]:
    """
    Return list of (start_pos, length, is_nan) tuples for a boolean NaN mask.
    """
    runs = []
    if series.empty:
        return runs
    mask = series.isna().values
    i = 0
    while i < len(mask):
        j = i
        while j < len(mask) and mask[j] == mask[i]:
            j += 1
        runs.append((i, j - i, bool(mask[i])))
        i = j
    return runs


def _same_slot_median(
    meter_series: pd.Series,
    slot_ts: pd.Timestamp,
    lookback_days: int = 28,
) -> float:
    """
    Compute the rolling same-slot (hour × day-of-week) median for a given slot.

    Parameters
    ----------
    meter_series : pd.Series
        Full kwh series for one meter, indexed by timestamp.
    slot_ts : pd.Timestamp
        The timestamp of the missing slot.
    lookback_days : int
        How many days back to look for same-slot values.

    Returns
    -------
    float
        Median of available same-slot values, or NaN if none found.
    """
    target_hour = slot_ts.hour
    target_dow = slot_ts.dayofweek
    cutoff = slot_ts - pd.Timedelta(days=lookback_days)

    same_slot = meter_series[
        (meter_series.index < slot_ts)
        & (meter_series.index >= cutoff)
        & (meter_series.index.hour == target_hour)
        & (meter_series.index.dayofweek == target_dow)
    ].dropna()

    if same_slot.empty:
        return np.nan
    return float(same_slot.median())


def handle_gaps(
    df: pd.DataFrame,
    flags_path: str = FLAGS_PATH_DEFAULT,
) -> pd.DataFrame:
    """
    Detect and handle missing readings per meter.

    Short gaps (1–3 consecutive NaN slots) are imputed with the 7-day
    same-slot rolling median. Extended gaps (>3 consecutive NaN slots)
    are flagged as HARDWARE_ISSUE and left as NaN.

    Parameters
    ----------
    df : pd.DataFrame
        Readings DataFrame from meter_reader / validator.
        Must have columns: meter_id, timestamp, kwh.
    flags_path : str
        Path to write hardware_issue_flags.csv.

    Returns
    -------
    pd.DataFrame
        DataFrame with short gaps filled. Extended-gap slots remain NaN.
        Original column order and dtypes preserved.
    """
    Path(flags_path).parent.mkdir(parents=True, exist_ok=True)

    result_frames: list[pd.DataFrame] = []
    hardware_flags: list[dict] = []

    for meter_id, group in df.groupby("meter_id", sort=False):
        group = group.sort_values("timestamp").copy()
        group = group.set_index("timestamp")

        # Reindex to complete 15-minute grid
        full_index = pd.date_range(
            start=group.index.min(),
            end=group.index.max(),
            freq="15min",
            tz=group.index.tz,
        )
        group = group.reindex(full_index)
        group["meter_id"] = meter_id

        kwh_series = group["kwh"].copy()
        runs = _run_length_encode(kwh_series)

        pos = 0
        for start_pos, length, is_nan in runs:
            if is_nan:
                gap_timestamps = kwh_series.index[start_pos: start_pos + length]
                gap_start = gap_timestamps[0]
                gap_end = gap_timestamps[-1]

                if length <= 3:
                    # Short gap — impute with 7-day same-slot median
                    for ts in gap_timestamps:
                        median_val = _same_slot_median(kwh_series, ts)
                        kwh_series.at[ts] = median_val
                else:
                    # Extended gap — flag as HARDWARE_ISSUE, do NOT impute
                    hardware_flags.append({
                        "meter_id": str(meter_id),
                        "gap_start": gap_start.isoformat(),
                        "gap_end": gap_end.isoformat(),
                        "gap_length_slots": length,
                    })
            pos += length

        group["kwh"] = kwh_series
        group = group.reset_index().rename(columns={"index": "timestamp"})
        result_frames.append(group)

    # Write hardware issue flags (always write header, even if empty)
    flags_df = pd.DataFrame(hardware_flags, columns=FLAGS_COLUMNS)
    flags_df.to_csv(flags_path, index=False)

    if not result_frames:
        return df

    result = pd.concat(result_frames, ignore_index=True)
    result.sort_values(["meter_id", "timestamp"], inplace=True)
    result.reset_index(drop=True, inplace=True)

    return result
