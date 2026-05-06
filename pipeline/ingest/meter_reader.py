"""
pipeline/ingest/meter_reader.py

Parse 15-minute interval smart meter readings from one or more CSV files.
Validates schema on load and returns a typed, sorted DataFrame.
"""

from __future__ import annotations

import pandas as pd
from pathlib import Path
from typing import Union

REQUIRED_COLUMNS = [
    "meter_id",
    "timestamp",
    "kwh",
    "voltage",
    "power_factor",
    "reactive_power",
]


def load_readings(file_paths: Union[str, list[str]]) -> pd.DataFrame:
    """
    Parse one or more CSV files of 15-minute interval smart meter readings.

    Parameters
    ----------
    file_paths : str or list[str]
        Path(s) to CSV file(s) containing meter readings.

    Returns
    -------
    pd.DataFrame
        Combined, typed, sorted DataFrame with columns:
        meter_id (str), timestamp (datetime64[ns, UTC]),
        kwh (float64), voltage (float64),
        power_factor (float64), reactive_power (float64).

    Raises
    ------
    ValueError
        If any required column is absent from a CSV file.
    FileNotFoundError
        If a specified file path does not exist.
    """
    if isinstance(file_paths, (str, Path)):
        file_paths = [file_paths]

    frames: list[pd.DataFrame] = []

    for path in file_paths:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Readings file not found: {path}")

        df = pd.read_csv(path)

        # Validate required columns
        for col in REQUIRED_COLUMNS:
            if col not in df.columns:
                raise ValueError(
                    f"Missing required column '{col}' in file: {path}"
                )

        # Cast types
        df["meter_id"] = df["meter_id"].astype(str)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        for col in ("kwh", "voltage", "power_factor", "reactive_power"):
            df[col] = df[col].astype("float64")

        frames.append(df[REQUIRED_COLUMNS])

    if not frames:
        raise ValueError("No files provided to load_readings()")

    combined = pd.concat(frames, ignore_index=True)
    combined.sort_values(["meter_id", "timestamp"], inplace=True)
    combined.reset_index(drop=True, inplace=True)

    return combined
