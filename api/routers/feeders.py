"""
api/routers/feeders.py

Feeder endpoints:
- GET /api/v1/feeders — all feeders with utilization %, stress_level, and 24h forecast
"""

from __future__ import annotations

import json
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models import FeederStatus

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ForecastPoint(BaseModel):
    timestamp: str
    predicted_utilization_pct: float


class FeederStatusResponse(BaseModel):
    feeder_id: str
    current_utilization_pct: float
    stress_level: Literal["GREEN", "AMBER", "RED"]
    transformer_rated_kva: Optional[float] = None
    forecast_24h: List[ForecastPoint] = []

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _compute_stress_level(utilization_pct: float) -> str:
    """
    Compute stress level from utilization percentage.
    Applied at serve time for accuracy.
    """
    if utilization_pct <= 70.0:
        return "GREEN"
    elif utilization_pct <= 90.0:
        return "AMBER"
    else:
        return "RED"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/feeders", response_model=List[FeederStatusResponse])
async def list_feeders(
    db: AsyncSession = Depends(get_db),
) -> List[FeederStatusResponse]:
    """
    Return all feeders with current utilization %, stress_level (GREEN/AMBER/RED),
    and 24-hour load forecast.

    stress_level is recomputed at serve time:
    - <= 70%  → GREEN
    - 70-90%  → AMBER
    - > 90%   → RED
    """
    result = await db.execute(select(FeederStatus))
    feeders = result.scalars().all()

    response = []
    for f in feeders:
        pct = float(f.current_utilization_pct or 0.0)
        stress = _compute_stress_level(pct)

        # Parse forecast JSON
        forecast: List[ForecastPoint] = []
        if f.forecast_24h:
            try:
                raw = json.loads(f.forecast_24h)
                forecast = [ForecastPoint(**pt) for pt in raw if isinstance(pt, dict)]
            except (json.JSONDecodeError, TypeError, ValueError):
                forecast = []

        response.append(
            FeederStatusResponse(
                feeder_id=f.feeder_id,
                current_utilization_pct=pct,
                stress_level=stress,
                transformer_rated_kva=f.transformer_rated_kva,
                forecast_24h=forecast,
            )
        )

    return response
