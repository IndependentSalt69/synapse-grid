"""
pipeline/features/pattern_fingerprint.py

Compute vacation/bypass disambiguation features:
- is_sustained_multiday_drop: True when ≥50% below baseline for ≥3 consecutive days
- night_activity_score: mean(22:00-05:00 kwh) / overall_mean
- is_recurring_daily_pattern: consistent hourly dip on ≥3 of last 5 days
  (overridden to False for vacation pattern: sustained drop + low night activity)
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_pattern_fingerprints(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute vacation/bypass disambiguation features for each meter reading.

    Parameters
    ----------
    df : pd.DataFrame
        Readings with columns: meter_id, timestamp, kwh.
        Should also have baseline_kwh (from deviations step) or pct_deviation_from_baseline.

    Returns
    -------
    pd.DataFrame
        Input DataFrame with added columns:
        is_sustained_multiday_drop (bool),
        night_activity_score (float),
        is_recurring_daily_pattern (bool).
    """
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["hour_of_day"] = df["timestamp"].dt.hour
    df["date"] = df["timestamp"].dt.date

    result_frames = []

    for meter_id, meter_df in df.groupby("meter_id", sort=False):
        meter_df = meter_df.sort_values("timestamp").copy()
        meter_df = _compute_sustained_drop(meter_df)
        meter_df = _compute_night_activity(meter_df)
        meter_df = _compute_recurring_pattern(meter_df)
        result_frames.append(meter_df)

    result = pd.concat(result_frames, ignore_index=True)
    result = result.sort_values(["meter_id", "timestamp"]).reset_index(drop=True)
    return result


def _compute_sustained_drop(meter_df: pd.DataFrame) -> pd.DataFrame:
    """
    is_sustained_multiday_drop = True when daily mean kwh is ≥50% below
    daily mean baseline for ≥3 consecutive days.
    """
    # Compute daily mean kwh and daily mean baseline
    if "baseline_kwh" in meter_df.columns:
        daily = meter_df.groupby("date").agg(
            daily_kwh=("kwh", "mean"),
            daily_baseline=("baseline_kwh", "mean"),
        ).reset_index()
        daily["daily_drop"] = (
            daily["daily_baseline"].notna()
            & (daily["daily_baseline"] > 0)
            & (daily["daily_kwh"] <= daily["daily_baseline"] * 0.50)
        )
    elif "pct_deviation_from_baseline" in meter_df.columns:
        daily = meter_df.groupby("date").agg(
            mean_pct_dev=("pct_deviation_from_baseline", "mean"),
        ).reset_index()
        daily["daily_drop"] = daily["mean_pct_dev"] <= -50.0
    else:
        meter_df["is_sustained_multiday_drop"] = False
        return meter_df

    # Find runs of ≥3 consecutive daily_drop=True days
    daily = daily.sort_values("date").reset_index(drop=True)
    sustained_dates = set()
    n = len(daily)
    i = 0
    while i < n:
        if daily.at[i, "daily_drop"]:
            j = i
            while j < n and daily.at[j, "daily_drop"]:
                j += 1
            run_length = j - i
            if run_length >= 3:
                for k in range(i, j):
                    sustained_dates.add(daily.at[k, "date"])
            i = j
        else:
            i += 1

    meter_df["is_sustained_multiday_drop"] = meter_df["date"].isin(sustained_dates)
    return meter_df


def _compute_night_activity(meter_df: pd.DataFrame) -> pd.DataFrame:
    """
    night_activity_score = mean(kwh where hour in [22,23,0,1,2,3,4]) / overall_mean.
    """
    night_hours = {22, 23, 0, 1, 2, 3, 4}
    night_mask = meter_df["hour_of_day"].isin(night_hours)
    night_mean = meter_df.loc[night_mask, "kwh"].mean()
    overall_mean = meter_df["kwh"].mean()

    if pd.isna(overall_mean) or overall_mean == 0:
        meter_df["night_activity_score"] = np.nan
    else:
        meter_df["night_activity_score"] = float(night_mean / overall_mean)

    return meter_df


def _compute_recurring_pattern(meter_df: pd.DataFrame) -> pd.DataFrame:
    """
    is_recurring_daily_pattern = True when a consumption dip (kwh < 50% of baseline)
    repeats at consistent hours (same ±1 hour window) on ≥3 of the last 5 days.

    Vacation override: if is_sustained_multiday_drop=True AND night_activity_score < 0.2,
    set is_recurring_daily_pattern=False (vacation, not bypass).
    """
    if "baseline_kwh" not in meter_df.columns:
        meter_df["is_recurring_daily_pattern"] = False
        return meter_df

    # Mark dip slots: kwh < 50% of baseline
    meter_df["is_dip"] = (
        meter_df["baseline_kwh"].notna()
        & (meter_df["baseline_kwh"] > 0)
        & (meter_df["kwh"] < meter_df["baseline_kwh"] * 0.50)
    )

    dates = sorted(meter_df["date"].unique())
    date_to_idx = {d: i for i, d in enumerate(dates)}
    recurring_dates = set()

    for day_idx, target_date in enumerate(dates):
        # Look at last 5 days including today: [D-4, D-3, D-2, D-1, D]
        window_dates = [
            dates[j] for j in range(max(0, day_idx - 4), day_idx + 1)
        ]
        if len(window_dates) < 3:
            continue

        # Find dip hours on target date
        target_dips = meter_df[
            (meter_df["date"] == target_date) & meter_df["is_dip"]
        ]["hour_of_day"].tolist()

        if not target_dips:
            continue

        # For each dip hour on target date, check if it recurs on ≥3 of 5 window days
        for dip_hour in set(target_dips):
            days_with_dip = 0
            for wd in window_dates:
                wd_dips = meter_df[
                    (meter_df["date"] == wd) & meter_df["is_dip"]
                ]["hour_of_day"].tolist()
                # Check if any dip within ±1 hour of dip_hour
                if any(abs(h - dip_hour) <= 1 for h in wd_dips):
                    days_with_dip += 1
            if days_with_dip >= 3:
                recurring_dates.add(target_date)
                break

    meter_df["is_recurring_daily_pattern"] = meter_df["date"].isin(recurring_dates)

    # Vacation override: sustained drop + near-zero night activity → NOT bypass
    if "is_sustained_multiday_drop" in meter_df.columns and "night_activity_score" in meter_df.columns:
        night_score = meter_df["night_activity_score"].iloc[0] if len(meter_df) > 0 else np.nan
        vacation_mask = (
            meter_df["is_sustained_multiday_drop"]
            & meter_df["night_activity_score"].notna()
            & (meter_df["night_activity_score"] < 0.2)
        )
        meter_df.loc[vacation_mask, "is_recurring_daily_pattern"] = False

    return meter_df
