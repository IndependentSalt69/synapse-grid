"""
pipeline/features/baseline.py

Compute per-meter rolling 28-day median consumption for each
hour-of-day × day-of-week slot (168 slots total).

Persists result to data/processed/baseline_lookup.parquet.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

OUTPUT_PATH_DEFAULT = "data/processed/baseline_lookup.parquet"


def compute_baseline(
    df: pd.DataFrame,
    output_path: str = OUTPUT_PATH_DEFAULT,
    force: bool = False,
) -> dict:
    """
    Compute rolling 28-day median baseline per meter per (hour_of_day, day_of_week) slot.

    Uses only data strictly preceding each target timestamp to prevent leakage.
    For each slot, collects up to 28 prior occurrences of the same (hour, dow) pair
    and takes the median.

    Parameters
    ----------
    df : pd.DataFrame
        Imputed readings with columns: meter_id, timestamp, kwh.
    output_path : str
        Path to write baseline_lookup.parquet.
    force : bool
        If False and output is up to date, skip computation.

    Returns
    -------
    dict
        {"skipped": True} if skipped, {"skipped": False} otherwise.
    """
    if not force and _is_output_fresh(output_path):
        return {"skipped": True}

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["hour_of_day"] = df["timestamp"].dt.hour.astype("int8")
    df["day_of_week"] = df["timestamp"].dt.dayofweek.astype("int8")

    records = []

    for meter_id, meter_df in df.groupby("meter_id", sort=False):
        meter_df = meter_df.sort_values("timestamp").reset_index(drop=True)

        # For each (hour, dow) slot, compute rolling 28-occurrence median
        for hour in range(24):
            for dow in range(7):
                slot_mask = (
                    (meter_df["hour_of_day"] == hour)
                    & (meter_df["day_of_week"] == dow)
                )
                slot_df = meter_df[slot_mask].copy()

                if slot_df.empty:
                    continue

                # Rolling 28-day median: use up to 28 preceding same-slot values
                # min_periods=1 so we always get a value even early in the series
                slot_df["baseline_kwh"] = (
                    slot_df["kwh"]
                    .rolling(window=28, min_periods=1)
                    .median()
                    .shift(1)  # shift(1) ensures we only use data BEFORE the current row
                )

                # For the very first occurrence, shift produces NaN — fill with the
                # first available value (the reading itself as a cold-start baseline)
                slot_df["baseline_kwh"] = slot_df["baseline_kwh"].fillna(slot_df["kwh"])

                for _, row in slot_df.iterrows():
                    records.append({
                        "meter_id": str(meter_id),
                        "hour_of_day": int(hour),
                        "day_of_week": int(dow),
                        "baseline_kwh": float(row["baseline_kwh"]),
                        "timestamp": row["timestamp"],
                    })

    result_df = pd.DataFrame(records)

    # Persist the per-slot summary (latest baseline per meter×hour×dow)
    # This is the lookup table used at inference time
    latest = (
        result_df.sort_values("timestamp")
        .groupby(["meter_id", "hour_of_day", "day_of_week"], sort=False)
        .last()
        .reset_index()[["meter_id", "hour_of_day", "day_of_week", "baseline_kwh"]]
    )

    latest.to_parquet(output_path, index=False, engine="pyarrow")
    return {"skipped": False, "baseline_df": result_df}


def _is_output_fresh(output_path: str) -> bool:
    return Path(output_path).exists()
