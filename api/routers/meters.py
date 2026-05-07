"""
api/routers/meters.py

Meter endpoints:
- GET /api/v1/meters/{meter_id}/readings  — last 14 days of 15-min readings
- GET /api/v1/meters/{meter_id}/baseline  — 28-day rolling baseline for charting
- GET /api/v1/meters/{meter_id}/peers     — peer meter IDs and current status
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import List, Literal, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models import MeterReading, MeterRegistryCache

router = APIRouter()

DATA_DIR = "data/processed"


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ReadingPoint(BaseModel):
    timestamp: str
    kwh: Optional[float] = None
    voltage: Optional[float] = None
    power_factor: Optional[float] = None


class BaselinePoint(BaseModel):
    hour_of_day: int
    day_of_week: int
    baseline_kwh: float


class PeerStatus(BaseModel):
    meter_id: str
    lat: float
    lng: float
    status: Literal["normal", "elevated", "anomalous"]


class MeterInfo(BaseModel):
    meter_id: str
    feeder_id: Optional[str] = None
    zone: Optional[str] = None
    consumer_category: Optional[str] = None
    sanctioned_kva: Optional[float] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/meters", response_model=List[MeterInfo])
async def list_meters(
    db: AsyncSession = Depends(get_db),
) -> List[MeterInfo]:
    """Return all meter IDs from the registry cache, sorted alphabetically."""
    result = await db.execute(
        select(MeterRegistryCache).order_by(MeterRegistryCache.meter_id)
    )
    rows = result.scalars().all()

    if not rows:
        raise HTTPException(
            status_code=503,
            detail="Meter registry not populated. Run 'python run_pipeline.py' first.",
        )

    return [
        MeterInfo(
            meter_id=r.meter_id,
            feeder_id=r.feeder_id,
            zone=r.zone,
            consumer_category=r.consumer_category,
            sanctioned_kva=r.sanctioned_kva,
            lat=r.lat,
            lng=r.lng,
        )
        for r in rows
    ]

@router.get("/meters/{meter_id}/readings", response_model=List[ReadingPoint])
async def get_meter_readings(
    meter_id: str,
    db: AsyncSession = Depends(get_db),
) -> List[ReadingPoint]:
    """
    Return last 14 days of 15-minute readings for the specified meter.

    Uses the most recent 14 days of data available for the meter rather than
    a wall-clock cutoff, so the endpoint works correctly with historical
    synthetic datasets whose timestamps are not near the current date.
    """
    import logging
    logger = logging.getLogger("synapse_grid.meters")

    # 14 days × 96 slots/day = 1,344 readings
    SLOTS_14_DAYS = 14 * 96

    # First check total row count for this meter (debug)
    count_stmt = select(MeterReading).where(MeterReading.meter_id == meter_id)
    count_result = await db.execute(count_stmt)
    all_rows = count_result.scalars().all()
    total_count = len(all_rows)
    logger.info(f"[readings] meter_id={meter_id!r} total_rows={total_count}")
    print(f"[DEBUG /readings] meter_id={meter_id!r}  total_rows_in_db={total_count}")

    if total_count == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No readings found for meter '{meter_id}'. "
                   "Run the pipeline first to populate meter_readings.",
        )

    # Return the most recent 1,344 slots (≈14 days), ascending for charting
    stmt = (
        select(MeterReading)
        .where(MeterReading.meter_id == meter_id)
        .order_by(MeterReading.timestamp.desc())
        .limit(SLOTS_14_DAYS)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    # Reverse so the chart gets ascending time order
    rows = list(reversed(rows))

    print(f"[DEBUG /readings] returning {len(rows)} rows  "
          f"first={rows[0].timestamp if rows else 'N/A'}  "
          f"last={rows[-1].timestamp if rows else 'N/A'}")

    return [
        ReadingPoint(
            timestamp=r.timestamp,
            kwh=r.kwh,
            voltage=r.voltage,
            power_factor=r.power_factor,
        )
        for r in rows
    ]


@router.get("/meters/{meter_id}/baseline", response_model=List[BaselinePoint])
async def get_meter_baseline(meter_id: str) -> List[BaselinePoint]:
    """Return the 28-day rolling baseline for the specified meter."""
    import os
    baseline_path = f"{DATA_DIR}/baseline_lookup.parquet"

    if not os.path.exists(baseline_path):
        raise HTTPException(
            status_code=503,
            detail="Baseline data not yet computed. Run 'python run_pipeline.py' first.",
        )

    try:
        df = pd.read_parquet(baseline_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read baseline: {e}")

    meter_df = df[df["meter_id"] == meter_id]
    if meter_df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No baseline found for meter '{meter_id}'.",
        )

    return [
        BaselinePoint(
            hour_of_day=int(row.hour_of_day),
            day_of_week=int(row.day_of_week),
            baseline_kwh=float(row.baseline_kwh),
        )
        for row in meter_df.itertuples()
    ]


@router.get("/meters/{meter_id}/peers", response_model=List[PeerStatus])
async def get_meter_peers(
    meter_id: str,
    db: AsyncSession = Depends(get_db),
) -> List[PeerStatus]:
    """
    Return peer meter IDs and their current consumption status.

    Status classification:
    - normal:    within ±20% of baseline
    - elevated:  >20% above baseline
    - anomalous: >20% below baseline
    """
    import json
    import os

    peer_graph_path = f"{DATA_DIR}/peer_graph.json"
    if not os.path.exists(peer_graph_path):
        raise HTTPException(
            status_code=503,
            detail="Peer graph not yet computed. Run 'python run_pipeline.py' first.",
        )

    with open(peer_graph_path) as f:
        peer_graph = json.load(f)

    neighbor_ids = peer_graph.get(meter_id, [])
    if not neighbor_ids:
        return []

    # Load baseline for classification
    baseline_path = f"{DATA_DIR}/baseline_lookup.parquet"
    baseline_df = None
    if os.path.exists(baseline_path):
        try:
            baseline_df = pd.read_parquet(baseline_path)
        except Exception:
            baseline_df = None

    result = []
    for neighbor_id in neighbor_ids:
        # Get registry entry for lat/lng
        reg_result = await db.execute(
            select(MeterRegistryCache).where(MeterRegistryCache.meter_id == neighbor_id)
        )
        reg = reg_result.scalar_one_or_none()
        if not reg:
            continue

        # Get latest reading
        reading_result = await db.execute(
            select(MeterReading)
            .where(MeterReading.meter_id == neighbor_id)
            .order_by(MeterReading.timestamp.desc())
            .limit(1)
        )
        reading = reading_result.scalar_one_or_none()
        if not reading:
            continue

        # Classify status
        status = "normal"
        if baseline_df is not None and reading.kwh is not None:
            try:
                ts = datetime.fromisoformat(reading.timestamp)
                baseline_row = baseline_df[
                    (baseline_df["meter_id"] == neighbor_id)
                    & (baseline_df["hour_of_day"] == ts.hour)
                    & (baseline_df["day_of_week"] == ts.weekday())
                ]
                if not baseline_row.empty:
                    baseline_kwh = float(baseline_row["baseline_kwh"].iloc[0])
                    if baseline_kwh > 0:
                        pct_dev = (reading.kwh - baseline_kwh) / baseline_kwh * 100.0
                        if pct_dev < -20:
                            status = "anomalous"
                        elif pct_dev > 20:
                            status = "elevated"
            except Exception:
                pass

        result.append(
            PeerStatus(
                meter_id=neighbor_id,
                lat=float(reg.lat),
                lng=float(reg.lng),
                status=status,
            )
        )

    return result
