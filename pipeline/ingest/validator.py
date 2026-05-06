"""
pipeline/ingest/validator.py

Apply domain-rule validation to meter readings.
Flags violations and logs them to data_quality_log.db.
Does NOT drop any records — flags only.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH_DEFAULT = "data/processed/data_quality_log.db"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS quality_violations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    meter_id       TEXT    NOT NULL,
    timestamp      TEXT    NOT NULL,
    violation_type TEXT    NOT NULL,
    field_name     TEXT    NOT NULL,
    observed_value REAL
)
"""


def _ensure_db(db_path: str) -> sqlite3.Connection:
    """Open (or create) the quality log database and ensure the table exists."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(CREATE_TABLE_SQL)
    conn.commit()
    return conn


def validate_readings(
    df: pd.DataFrame,
    db_path: str = DB_PATH_DEFAULT,
) -> pd.DataFrame:
    """
    Validate meter readings against domain rules and log violations to SQLite.

    Rules applied (all in a single pass, no halting on first violation):
    1. kwh < 0                 -> NEGATIVE_KWH
    2. Non-monotonic timestamp per meter -> NON_MONOTONIC_TIMESTAMP
    3. voltage < 180 or > 260  -> VOLTAGE_OUT_OF_RANGE  (row NOT dropped)
    4. power_factor < 0 or > 1 -> POWER_FACTOR_OUT_OF_RANGE

    Parameters
    ----------
    df : pd.DataFrame
        Readings DataFrame from meter_reader.load_readings().
    db_path : str
        Path to the SQLite quality log database.

    Returns
    -------
    pd.DataFrame
        The original DataFrame, unmodified.
    """
    conn = _ensure_db(db_path)
    violations: list[tuple] = []

    # Work on a copy with string timestamps for logging
    ts_str = df["timestamp"].astype(str)

    # Rule 1 — Negative kWh
    neg_mask = df["kwh"] < 0
    for idx in df.index[neg_mask]:
        violations.append((
            df.at[idx, "meter_id"],
            ts_str.at[idx],
            "NEGATIVE_KWH",
            "kwh",
            float(df.at[idx, "kwh"]),
        ))

    # Rule 2 — Non-monotonic timestamp per meter.
    # Iterate rows in the order they appear in the DataFrame (do NOT sort).
    # A row is non-monotonic if its timestamp is <= the previous row for the same meter.
    for meter_id, group in df.groupby("meter_id", sort=False):
        prev_ts = None
        for idx, row in group.iterrows():
            ts = row["timestamp"]
            if prev_ts is not None and ts <= prev_ts:
                violations.append((
                    str(meter_id),
                    ts_str.at[idx],
                    "NON_MONOTONIC_TIMESTAMP",
                    "timestamp",
                    None,
                ))
            prev_ts = ts

    # Rule 3 — Voltage out of range (flag, do NOT drop)
    volt_mask = (df["voltage"] < 180) | (df["voltage"] > 260)
    for idx in df.index[volt_mask]:
        violations.append((
            df.at[idx, "meter_id"],
            ts_str.at[idx],
            "VOLTAGE_OUT_OF_RANGE",
            "voltage",
            float(df.at[idx, "voltage"]),
        ))

    # Rule 4 — Power factor out of range
    pf_mask = (df["power_factor"] < 0) | (df["power_factor"] > 1)
    for idx in df.index[pf_mask]:
        violations.append((
            df.at[idx, "meter_id"],
            ts_str.at[idx],
            "POWER_FACTOR_OUT_OF_RANGE",
            "power_factor",
            float(df.at[idx, "power_factor"]),
        ))

    # Commit all violations in one transaction
    if violations:
        conn.executemany(
            "INSERT INTO quality_violations "
            "(meter_id, timestamp, violation_type, field_name, observed_value) "
            "VALUES (?, ?, ?, ?, ?)",
            violations,
        )
        conn.commit()

    conn.close()
    return df
