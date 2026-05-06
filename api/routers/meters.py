"""
api/routers/meters.py

Meter endpoints:
- GET /api/v1/meters/{meter_id}/readings  — last 14 days of 15-min readings
- GET /api/v1/meters/{meter_id}/baseline  — 28-day rolling baseline for charting
- GET /api/v1/meters/{meter_id}/peers     — peer meter IDs and current status
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import List, Literal, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, and_
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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/meters/{meter_id}/readings", response_model=List[ReadingPoint])
async def get_meter_readings(
    meter_id: str,
    db: AsyncSession = Depends(get_db),
) -> List[ReadingPoint]:
    """Return last 14 days of 15-minute readings for the specified meter."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    stmt = (
        select(MeterReading)
        .where(
            and_(
                MeterReading.meter_id == meter_id,
                MeterReading.timestamp >= cutoff,
            )
        )
        .order_by(MeterReading.timestamp.asc())
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No readings found for meter '{meter_id}' in the last 14 days. "
                   "Run the pipeline first to populate meter_readings.",
        )

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
