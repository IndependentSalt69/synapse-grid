"""
pipeline/clustering/seasonal.py

Cluster meters into 8 groups based on seasonal load shape similarity.
Fits KMeans on month × hour load shape vectors (12 × 24 = 288 dimensions).

Persists:
- data/processed/cluster_assignments.csv
- data/processed/seasonal_profiles.json
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

ASSIGNMENTS_PATH_DEFAULT = "data/processed/cluster_assignments.csv"
PROFILES_PATH_DEFAULT = "data/processed/seasonal_profiles.json"
N_CLUSTERS = 8
RANDOM_STATE = 42


def fit_seasonal_clusters(
    df: pd.DataFrame,
    n_clusters: int = N_CLUSTERS,
    assignments_path: str = ASSIGNMENTS_PATH_DEFAULT,
    profiles_path: str = PROFILES_PATH_DEFAULT,
    force: bool = False,
) -> pd.DataFrame:
    """
    Cluster meters by seasonal load shape using KMeans.

    Parameters
    ----------
    df : pd.DataFrame
        Imputed readings with columns: meter_id, timestamp, kwh.
    n_clusters : int
        Number of clusters (default 8).
    assignments_path : str
        Path to write cluster_assignments.csv.
    profiles_path : str
        Path to write seasonal_profiles.json.
    force : bool
        If False and outputs exist, skip computation.

    Returns
    -------
    pd.DataFrame
        Cluster assignments with columns: meter_id, cluster_id.
    """
    if not force and Path(assignments_path).exists() and Path(profiles_path).exists():
        return {"skipped": True}

    Path(assignments_path).parent.mkdir(parents=True, exist_ok=True)

    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["month"] = df["timestamp"].dt.month
    df["hour_of_day"] = df["timestamp"].dt.hour

    meter_ids = df["meter_id"].unique().tolist()

    # Build month × hour load shape vector per meter (12 × 24 = 288 dims)
    vectors = []
    valid_meter_ids = []

    for meter_id in meter_ids:
        meter_df = df[df["meter_id"] == meter_id]
        # Compute mean kwh per (month, hour_of_day)
        shape = (
            meter_df.groupby(["month", "hour_of_day"])["kwh"]
            .mean()
            .unstack(fill_value=0.0)
        )
        # Ensure all 12 months and 24 hours are present
        shape = shape.reindex(index=range(1, 13), columns=range(24), fill_value=0.0)
        vector = shape.values.flatten()  # shape (288,)

        # Normalize by overall mean (unit-normalize the shape)
        overall_mean = meter_df["kwh"].mean()
        if overall_mean > 0:
            vector = vector / overall_mean

        vectors.append(vector)
        valid_meter_ids.append(meter_id)

    X = np.array(vectors)  # shape (n_meters, 288)

    # Fit KMeans
    kmeans = KMeans(n_clusters=n_clusters, random_state=RANDOM_STATE, n_init=10)
    labels = kmeans.fit_predict(X)

    # Build assignments DataFrame
    assignments = pd.DataFrame({
        "meter_id": valid_meter_ids,
        "cluster_id": labels.astype(int),
    })
    assignments.to_csv(assignments_path, index=False)

    # Build cluster centroid profiles: {cluster_id: {month: {hour: mean_kwh}}}
    profiles = {}
    for cluster_id in range(n_clusters):
        cluster_meters = assignments[assignments["cluster_id"] == cluster_id]["meter_id"].tolist()
        cluster_df = df[df["meter_id"].isin(cluster_meters)]
        if cluster_df.empty:
            profiles[str(cluster_id)] = {}
            continue
        shape = (
            cluster_df.groupby(["month", "hour_of_day"])["kwh"]
            .mean()
            .unstack(fill_value=0.0)
        )
        shape = shape.reindex(index=range(1, 13), columns=range(24), fill_value=0.0)
        profiles[str(cluster_id)] = {
            str(month): {str(hour): float(shape.at[month, hour]) for hour in range(24)}
            for month in range(1, 13)
        }

    with open(profiles_path, "w") as f:
        json.dump(profiles, f, indent=2)

    return assignments
