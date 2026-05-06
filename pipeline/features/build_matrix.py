"""
pipeline/features/build_matrix.py

Assemble the full feature matrix by joining all feature sources
per meter per timestamp. Persists to data/processed/feature_matrix.parquet.

Feature matrix columns:
meter_id, timestamp, kwh, cluster_id, feeder_id,
z_score, peer_deviation_score, is_sustained_multiday_drop,
is_recurring_daily_pattern, night_activity_score,
pct_deviation_from_baseline, pct_deviation_from_peer_median,
pct_deviation_from_cluster_norm,
lag_1h, lag_24h, lag_48h, lag_7d,
rolling_7d_mean, rolling_7d_std, trend_slope_3d,
is_high_stress_zone, confirmed_tamper
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

OUTPUT_PATH_DEFAULT = "data/processed/feature_matrix.parquet"

REQUIRED_SOURCES = {
    "baseline_lookup": "data/processed/baseline_lookup.parquet",
    "zone_profiles": "data/processed/zone_profiles.parquet",
    "cluster_assignments": "data/processed/cluster_assignments.csv",
    "peer_graph": "data/processed/peer_graph.json",
}

FEATURE_COLUMNS = [
    "meter_id", "timestamp", "kwh", "cluster_id", "feeder_id",
    "z_score", "peer_deviation_score",
    "is_sustained_multiday_drop", "is_recurring_daily_pattern",
    "night_activity_score",
    "pct_deviation_from_baseline", "pct_deviation_from_peer_median",
    "pct_deviation_from_cluster_norm",
    "lag_1h", "lag_24h", "lag_48h", "lag_7d",
    "rolling_7d_mean", "rolling_7d_std", "trend_slope_3d",
    "is_high_stress_zone", "confirmed_tamper",
]


def build_feature_matrix(
    readings_df: pd.DataFrame,
    registry_df: pd.DataFrame,
    data_dir: str = "data/processed",
    injected_events_path: str = "data/raw/injected_events.json",
    output_path: str = OUTPUT_PATH_DEFAULT,
    force: bool = False,
) -> pd.DataFrame:
    """
    Join all feature sources into a single feature matrix.

    Parameters
    ----------
    readings_df : pd.DataFrame
        Imputed readings (already through gap_handler).
    registry_df : pd.DataFrame
        Meter registry with feeder_id, transformer_id, etc.
    data_dir : str
        Directory containing processed feature files.
    injected_events_path : str
        Path to injected_events.json for confirmed_tamper labels.
    output_path : str
        Path to write feature_matrix.parquet.
    force : bool
        If False and output exists, skip.

    Returns
    -------
    pd.DataFrame
        Full feature matrix.
    """
    if not force and Path(output_path).exists():
        return {"skipped": True}

    # Check required source files
    for name, rel_path in REQUIRED_SOURCES.items():
        full_path = Path(rel_path)
        if not full_path.exists():
            raise FileNotFoundError(
                f"Required feature source missing: {full_path} ({name})"
            )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # --- Load source files ---
    baseline_df = pd.read_parquet(REQUIRED_SOURCES["baseline_lookup"])
    zone_df = pd.read_parquet(REQUIRED_SOURCES["zone_profiles"])
    cluster_df = pd.read_csv(REQUIRED_SOURCES["cluster_assignments"])
    with open(REQUIRED_SOURCES["peer_graph"]) as f:
        peer_graph = json.load(f)

    # --- Base: imputed readings ---
    from pipeline.features.deviations import compute_deviations
    from pipeline.features.temporal_lags import compute_lag_features
    from pipeline.features.pattern_fingerprint import compute_pattern_fingerprints

    df = readings_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

    # Join feeder_id from registry
    df = df.merge(
        registry_df[["meter_id", "feeder_id"]],
        on="meter_id",
        how="left",
    )

    # --- Deviations ---
    df = compute_deviations(df, baseline_df, peer_graph)

    # --- Lag features ---
    df = compute_lag_features(df)

    # --- Pattern fingerprints ---
    df = compute_pattern_fingerprints(df)

    # --- Cluster assignments ---
    df = df.merge(cluster_df[["meter_id", "cluster_id"]], on="meter_id", how="left")

    # --- Zone profiles: join is_high_stress_zone ---
    zone_slim = zone_df[["feeder_id", "timestamp", "is_high_stress_zone"]].copy()
    zone_slim["timestamp"] = pd.to_datetime(zone_slim["timestamp"], utc=True)
    df = df.merge(zone_slim, on=["feeder_id", "timestamp"], how="left")

    # --- pct_deviation_from_cluster_norm ---
    if "cluster_id" in df.columns:
        cluster_median = (
            df.groupby(["cluster_id", "timestamp"])["kwh"]
            .median()
            .reset_index()
            .rename(columns={"kwh": "cluster_median_kwh"})
        )
        df = df.merge(cluster_median, on=["cluster_id", "timestamp"], how="left")
        df["pct_deviation_from_cluster_norm"] = np.where(
            df["cluster_median_kwh"].notna() & (df["cluster_median_kwh"] > 0),
            (df["kwh"] - df["cluster_median_kwh"]) / df["cluster_median_kwh"] * 100.0,
            np.nan,
        )
        df.drop(columns=["cluster_median_kwh"], inplace=True, errors="ignore")

    # --- confirmed_tamper labels from injected_events.json ---
    df["confirmed_tamper"] = 0
    if Path(injected_events_path).exists():
        with open(injected_events_path) as f:
            events = json.load(f)
        start_date = df["timestamp"].min().normalize()
        for cfg in events.get("tamper_meters", []):
            tamper_start = start_date + pd.Timedelta(days=cfg["start_day"])
            tamper_end = start_date + pd.Timedelta(days=cfg["end_day"])
            mask = (
                (df["meter_id"] == cfg["meter_id"])
                & (df["timestamp"] >= tamper_start)
                & (df["timestamp"] < tamper_end)
            )
            df.loc[mask, "confirmed_tamper"] = 1

    # --- Ensure all feature columns exist (fill missing with NaN/False/0) ---
    for col in FEATURE_COLUMNS:
        if col not in df.columns:
            if col in ("is_sustained_multiday_drop", "is_recurring_daily_pattern", "is_high_stress_zone"):
                df[col] = False
            elif col == "confirmed_tamper":
                df[col] = 0
            else:
                df[col] = np.nan

    # Cast bool columns
    for col in ("is_sustained_multiday_drop", "is_recurring_daily_pattern", "is_high_stress_zone"):
        df[col] = df[col].fillna(False).astype(bool)
    df["confirmed_tamper"] = df["confirmed_tamper"].fillna(0).astype("int8")

    # Select and order final columns
    available = [c for c in FEATURE_COLUMNS if c in df.columns]
    result = df[available].copy()
    result = result.sort_values(["meter_id", "timestamp"]).reset_index(drop=True)

    result.to_parquet(output_path, index=False, engine="pyarrow")
    return result
