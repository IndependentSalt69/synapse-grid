"""
pipeline/features/temporal_lags.py

Build lag features and rolling statistics for each meter reading:
- lag_1h (4 slots back), lag_24h (96), lag_48h (192), lag_7d (672)
- rolling_7d_mean, rolling_7d_std
- trend_slope_3d (linear regression slope over 3-day same-slot window)
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute lag and rolling statistical features per meter.

    Parameters
    ----------
    df : pd.DataFrame
        Readings with columns: meter_id, timestamp, kwh (plus any existing columns).

    Returns
    -------
    pd.DataFrame
        Input DataFrame with added columns:
        lag_1h, lag_24h, lag_48h, lag_7d,
        rolling_7d_mean, rolling_7d_std, trend_slope_3d.
        NaN is used when insufficient history exists.
    """
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values(["meter_id", "timestamp"]).reset_index(drop=True)

    # --- Lag features via .shift() within each meter group ---
    df["lag_1h"] = df.groupby("meter_id")["kwh"].transform(
        lambda s: s.shift(4)       # 4 × 15 min = 1 hour
    )
    df["lag_24h"] = df.groupby("meter_id")["kwh"].transform(
        lambda s: s.shift(96)      # 96 × 15 min = 24 hours
    )
    df["lag_48h"] = df.groupby("meter_id")["kwh"].transform(
        lambda s: s.shift(192)     # 192 × 15 min = 48 hours
    )
    df["lag_7d"] = df.groupby("meter_id")["kwh"].transform(
        lambda s: s.shift(672)     # 672 × 15 min = 7 days
    )

    # --- Rolling 7-day mean and std ---
    df["rolling_7d_mean"] = df.groupby("meter_id")["kwh"].transform(
        lambda s: s.rolling(window=672, min_periods=96).mean()
    )
    df["rolling_7d_std"] = df.groupby("meter_id")["kwh"].transform(
        lambda s: s.rolling(window=672, min_periods=96).std()
    )

    # --- 3-day trend slope ---
    # For each row, collect the same (hour_of_day, day_of_week) slot values
    # from the preceding 3 days (3 data points) and fit a linear slope.
    df["hour_of_day"] = df["timestamp"].dt.hour
    df["day_of_week"] = df["timestamp"].dt.dayofweek

    trend_slopes = _compute_trend_slopes(df)
    df["trend_slope_3d"] = trend_slopes

    # Clean up helper columns if they weren't in the original
    return df


def _compute_trend_slopes(df: pd.DataFrame) -> pd.Series:
    """
    Compute 3-day trend slope for each row using same-slot history.

    For each (meter_id, hour_of_day, day_of_week) group, collect the
    3 most recent prior values and fit a linear regression slope.
    """
    slopes = pd.Series(np.nan, index=df.index, dtype="float64")

    for (meter_id, hour, dow), group in df.groupby(
        ["meter_id", "hour_of_day", "day_of_week"], sort=False
    ):
        group = group.sort_values("timestamp")
        kwh_vals = group["kwh"].values
        indices = group.index.tolist()

        for i, idx in enumerate(indices):
            if i < 2:
                # Need at least 2 prior points for a slope
                slopes.at[idx] = np.nan
                continue
            # Use up to 3 preceding same-slot values (not including current)
            window = kwh_vals[max(0, i - 3): i]
            valid = window[~np.isnan(window)]
            if len(valid) < 2:
                slopes.at[idx] = np.nan
                continue
            x = np.arange(len(valid), dtype="float64")
            slope = np.polyfit(x, valid, 1)[0]
            slopes.at[idx] = float(slope)

    return slopes
