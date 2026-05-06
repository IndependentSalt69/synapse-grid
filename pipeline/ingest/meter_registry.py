"""
pipeline/ingest/meter_registry.py

Load and serve the meter registry CSV.
Provides schema validation and an O(1) lookup interface.
"""

from __future__ import annotations

import pandas as pd
from datetime import date
from pathlib import Path
from typing import Union

REQUIRED_COLUMNS = [
    "meter_id",
    "lat",
    "lng",
    "feeder_id",
    "transformer_id",
    "zone",
    "consumer_category",
    "sanctioned_kva",
    "connection_date",
]


def load_registry(
    file_path: Union[str, Path],
) -> tuple[pd.DataFrame, dict]:
    """
    Load the meter registry CSV and return a DataFrame plus an O(1) lookup dict.

    Parameters
    ----------
    file_path : str or Path
        Path to the registry CSV file.

    Returns
    -------
    tuple[pd.DataFrame, dict]
        - df: typed DataFrame with all registry columns.
        - registry: dict mapping meter_id → {column: value} for O(1) lookup.

    Raises
    ------
    FileNotFoundError
        If the registry file does not exist.
    ValueError
        If any required column is absent, or if sanctioned_kva is non-positive.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Registry file not found: {file_path}")

    df = pd.read_csv(file_path)

    # Validate required columns
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            raise ValueError(
                f"Missing required column '{col}' in registry file: {file_path}"
            )

    # Cast types
    df["meter_id"] = df["meter_id"].astype(str)
    df["lat"] = df["lat"].astype("float64")
    df["lng"] = df["lng"].astype("float64")
    df["feeder_id"] = df["feeder_id"].astype(str)
    df["transformer_id"] = df["transformer_id"].astype(str)
    df["zone"] = df["zone"].astype(str)
    df["consumer_category"] = df["consumer_category"].astype(str)
    df["sanctioned_kva"] = df["sanctioned_kva"].astype("float64")
    df["connection_date"] = pd.to_datetime(df["connection_date"]).dt.date

    # Validate sanctioned_kva is positive
    invalid_kva = df[df["sanctioned_kva"] <= 0]
    if not invalid_kva.empty:
        bad_ids = invalid_kva["meter_id"].tolist()
        raise ValueError(
            f"sanctioned_kva must be positive. Invalid meters: {bad_ids}"
        )

    # Build O(1) lookup dict: meter_id → {col: value}
    registry = df.set_index("meter_id").to_dict(orient="index")

    return df, registry


def get_meter(registry: dict, meter_id: str) -> dict:
    """
    Look up a single meter's metadata in O(1) time.

    Parameters
    ----------
    registry : dict
        The lookup dict returned by load_registry().
    meter_id : str
        The meter ID to look up.

    Returns
    -------
    dict
        Metadata record for the meter.

    Raises
    ------
    KeyError
        If meter_id is not found in the registry.
    """
    if meter_id not in registry:
        raise KeyError(f"Meter '{meter_id}' not found in registry")
    return registry[meter_id]
