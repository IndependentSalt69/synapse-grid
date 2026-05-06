"""
pipeline/features/deviations.py

Compute deviation features for each meter reading:
- pct_deviation_from_baseline
- z_score
- peer_deviation_score / pct_deviation_from_peer_median
- peer_deviation_flag
"""

from __future__ import annotations

import json
from math import ceil
from pathlib import Path

import numpy as np
import pandas as pd


def compute_deviations(
    df: pd.DataFrame,
    baseline_df: pd.DataFrame,
    peer_graph: dict[str, list[str]],
) -> pd.DataFrame:
    """
    Compute deviation metrics for each meter reading.

    Parameters
    ----------
    df : pd.DataFrame
        Imputed readings with columns: meter_id, timestamp, kwh.
    baseline_df : pd.DataFrame
        Baseline lookup with columns: meter_id, hour_of_day, day_of_week, baseline_kwh.
    peer_graph : dict
        Adjacency dict {meter_id: [neighbor_ids]}.

    Returns
    -------
    pd.DataFrame
        Input DataFrame with added columns:
        pct_deviation_from_baseline, z_score, peer_deviation_score,
        pct_deviation_from_peer_median, peer_deviation_flag.
    """
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["hour_of_day"] = df["timestamp"].dt.hour.astype("int8")
    df["day_of_week"] = df["timestamp"].dt.dayofweek.astype("int8")

    # --- pct_deviation_from_baseline ---
    df = df.merge(
        baseline_df[["meter_id", "hour_of_day", "day_of_week", "baseline_kwh"]],
        on=["meter_id", "hour_of_day", "day_of_week"],
        how="left",
    )
    df["pct_deviation_from_baseline"] = np.where(
        df["baseline_kwh"].notna() & (df["baseline_kwh"] != 0),
        (df["kwh"] - df["baseline_kwh"]) / df["baseline_kwh"] * 100.0,
        np.nan,
    )

    # --- z_score (rolling 28-day mean and std per meter) ---
    # 28 days × 96 slots/day = 2688 slots
    df = df.sort_values(["meter_id", "timestamp"])
    df["rolling_28d_mean"] = (
        df.groupby("meter_id")["kwh"]
        .transform(lambda s: s.rolling(window=2688, min_periods=96).mean())
    )
    df["rolling_28d_std"] = (
        df.groupby("meter_id")["kwh"]
        .transform(lambda s: s.rolling(window=2688, min_periods=96).std())
    )
    df["z_score"] = np.where(
        df["rolling_28d_std"].notna() & (df["rolling_28d_std"] > 0),
        (df["kwh"] - df["rolling_28d_mean"]) / df["rolling_28d_std"],
        np.nan,
    )

    # --- peer_deviation_score / pct_deviation_from_peer_median ---
    # Build a pivot: timestamp → meter_id → kwh for fast neighbor lookups
    pivot = df.pivot_table(
        index="timestamp", columns="meter_id", values="kwh", aggfunc="first"
    )

    peer_scores = []
    peer_flags = []

    for _, row in df.iterrows():
        meter_id = row["meter_id"]
        ts = row["timestamp"]
        kwh = row["kwh"]
        neighbors = peer_graph.get(str(meter_id), [])

        if not neighbors or pd.isna(kwh):
            peer_scores.append(np.nan)
            peer_flags.append(False)
            continue

        # Get neighbor kwh values at the same timestamp
        neighbor_kwh = []
        for n in neighbors:
            if n in pivot.columns and ts in pivot.index:
                val = pivot.at[ts, n]
                if not pd.isna(val):
                    neighbor_kwh.append(val)

        if not neighbor_kwh:
            peer_scores.append(np.nan)
            peer_flags.append(False)
            continue

        neighbor_median = float(np.median(neighbor_kwh))
        if neighbor_median == 0:
            peer_scores.append(np.nan)
            peer_flags.append(False)
            continue

        score = (kwh - neighbor_median) / neighbor_median * 100.0
        peer_scores.append(score)

        # Peer deviation flag:
        # meter drops ≥80% below baseline AND ≥75% of neighbors are stable/rising
        pct_dev = row.get("pct_deviation_from_baseline", np.nan)
        if pd.isna(pct_dev) or pct_dev > -80:
            peer_flags.append(False)
            continue

        # Count neighbors that are stable or rising (kwh >= their baseline * 0.95)
        # We use the neighbor's own kwh vs the overall neighbor median as a proxy
        # (full baseline join would be too expensive here; use neighbor_median as reference)
        stable_count = sum(
            1 for v in neighbor_kwh if v >= neighbor_median * 0.95
        )
        required = ceil(0.75 * len(neighbors))
        peer_flags.append(stable_count >= required)

    df["peer_deviation_score"] = peer_scores
    df["pct_deviation_from_peer_median"] = peer_scores  # alias
    df["peer_deviation_flag"] = peer_flags

    # Drop helper columns not needed downstream
    df.drop(columns=["rolling_28d_mean", "rolling_28d_std"], inplace=True, errors="ignore")

    return df
