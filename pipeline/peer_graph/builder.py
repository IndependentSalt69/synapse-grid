"""
pipeline/peer_graph/builder.py

Build a geographic peer graph: for each meter, find all other meters
within 200m radius using the Haversine formula on lat/lng coordinates.

Persists the adjacency dict to data/processed/peer_graph.json.
"""

from __future__ import annotations

import json
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Union

import pandas as pd

PEER_RADIUS_METERS = 200.0
EARTH_RADIUS_METERS = 6_371_000.0
OUTPUT_PATH_DEFAULT = "data/processed/peer_graph.json"


def haversine_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Compute the Haversine distance in metres between two lat/lng points.

    Parameters
    ----------
    lat1, lng1 : float
        Coordinates of point 1 in decimal degrees.
    lat2, lng2 : float
        Coordinates of point 2 in decimal degrees.

    Returns
    -------
    float
        Distance in metres.
    """
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lng2 - lng1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_METERS * asin(sqrt(a))


def build_peer_graph(
    registry_df: pd.DataFrame,
    output_path: str = OUTPUT_PATH_DEFAULT,
    radius_m: float = PEER_RADIUS_METERS,
) -> dict[str, list[str]]:
    """
    Build a geographic adjacency graph of meters within `radius_m` of each other.

    Parameters
    ----------
    registry_df : pd.DataFrame
        Registry DataFrame with columns: meter_id, lat, lng.
    output_path : str
        Path to write the peer_graph.json file.
    radius_m : float
        Radius in metres for peer detection (default 200m).

    Returns
    -------
    dict[str, list[str]]
        Adjacency dict: {meter_id: [neighbor_meter_id, ...]}
        Meters with no neighbors within radius have an empty list.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    meter_ids = registry_df["meter_id"].tolist()
    lats = registry_df["lat"].tolist()
    lngs = registry_df["lng"].tolist()
    n = len(meter_ids)

    # Initialise adjacency dict with empty lists
    adjacency: dict[str, list[str]] = {mid: [] for mid in meter_ids}

    # Compute pairwise distances (upper triangle only, then mirror)
    for i in range(n):
        for j in range(i + 1, n):
            dist = haversine_distance(lats[i], lngs[i], lats[j], lngs[j])
            if dist <= radius_m:
                adjacency[meter_ids[i]].append(meter_ids[j])
                adjacency[meter_ids[j]].append(meter_ids[i])

    # Persist to JSON (idempotent — overwrites on re-run)
    with open(output_path, "w") as f:
        json.dump(adjacency, f, indent=2)

    return adjacency
