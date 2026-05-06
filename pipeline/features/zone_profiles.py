"""
pipeline/features/zone_profiles.py

Aggregate per-feeder hourly load profiles and compute stress metrics:
- total_load_kwh per feeder per timestamp
- feeder_stress = total_load_kwh / transformer_rated_kva
- is_high_stress_zone = feeder_stress > 0.90
- pct_deviation_from_cluster_norm per meter

Persists to data/processed/zone_profiles.parquet.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

OUTPUT_PATH_DEFAULT = "data/processed/zone_profiles.parquet"


def compute_zone_profiles(
    df: pd.DataFrame,
    registry_df: pd.DataFrame,
    cluster_assignments: pd.DataFrame | None = None,
    output_path: str = OUTPUT_PATH_DEFAULT,
    force: bool = False,
) -> pd.DataFrame:
    """
    Compute per-feeder load profiles and stress metrics.

    Parameters
    ----------
    df : pd.DataFrame
        Imputed readings with columns: meter_id, timestamp, kwh.
    registry_df : pd.DataFrame
        Registry with columns: meter_id, feeder_id, transformer_id, sanctioned_kva.
    cluster_assignments : pd.DataFrame or None
        Optional cluster assignments with columns: meter_id, cluster_id.
        Used to compute pct_deviation_from_cluster_norm.
    output_path : str
        Path to write zone_profiles.parquet.
    force : bool
        If False and output is up to date, skip computation.

    Returns
    -------
    pd.DataFrame
        Zone profiles with columns:
        feeder_id, timestamp, total_load_kwh, feeder_stress,
        is_high_stress_zone, transformer_rated_kva.
    """
    if not force and Path(output_path).exists():
        return {"skipped": True}

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

    # Join feeder and transformer info from registry
    registry_slim = registry_df[
        ["meter_id", "feeder_id", "transformer_id", "sanctioned_kva"]
    ].copy()
    df = df.merge(registry_slim, on="meter_id", how="left")

    # Compute transformer rated kva: sum of sanctioned_kva for all meters on each transformer
    transformer_kva = (
        registry_slim.groupby("transformer_id")["sanctioned_kva"]
        .sum()
        .rename("transformer_rated_kva")
        .reset_index()
    )

    # Map transformer_rated_kva to each feeder
    feeder_transformer = registry_slim[["feeder_id", "transformer_id"]].drop_duplicates()
    feeder_transformer = feeder_transformer.merge(transformer_kva, on="transformer_id", how="left")

    # Aggregate per-feeder load per timestamp
    feeder_load = (
        df.groupby(["feeder_id", "timestamp"])["kwh"]
        .sum()
        .reset_index()
        .rename(columns={"kwh": "total_load_kwh"})
    )

    # Join transformer capacity
    feeder_load = feeder_load.merge(
        feeder_transformer[["feeder_id", "transformer_rated_kva"]],
        on="feeder_id",
        how="left",
    )

    # Compute stress metrics
    feeder_load["feeder_stress"] = np.where(
        feeder_load["transformer_rated_kva"].notna() & (feeder_load["transformer_rated_kva"] > 0),
        feeder_load["total_load_kwh"] / feeder_load["transformer_rated_kva"],
        np.nan,
    )
    feeder_load["is_high_stress_zone"] = feeder_load["feeder_stress"] > 0.90

    # Compute pct_deviation_from_cluster_norm if cluster assignments provided
    if cluster_assignments is not None:
        df = df.merge(cluster_assignments[["meter_id", "cluster_id"]], on="meter_id", how="left")
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

    feeder_load.to_parquet(output_path, index=False, engine="pyarrow")
    return feeder_load
