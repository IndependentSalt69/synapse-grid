"""
api/routers/alerts.py

Alert endpoints:
- GET  /api/v1/alerts              — list alerts, filterable, sorted by confidence DESC
- GET  /api/v1/alerts/{alert_id}   — full alert detail with shap_top3 and peer_status_summary
- PATCH /api/v1/alerts/{alert_id}/action — dispatch action with business rule validation
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models import AlertEvent, DispatchAuditLog

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ShapEntry(BaseModel):
    feature: str
    value: float
    plain_english: str


class AlertSummary(BaseModel):
    alert_id: str
    meter_id: str
    alert_type: str
    state: str
    anomaly_confidence: float
    pattern_type: Optional[str] = None
    triggered_at: str
    feeder_id: Optional[str] = None

    class Config:
        from_attributes = True


class AlertDetail(BaseModel):
    alert_id: str
    meter_id: str
    alert_type: str
    state: str
    anomaly_confidence: float
    pattern_type: Optional[str] = None
    triggered_at: str
    pct_deviation_from_baseline: Optional[float] = None
    pct_deviation_from_peer_median: Optional[float] = None
    pct_deviation_from_cluster_norm: Optional[float] = None
    z_score: Optional[float] = None
    shap_top3: List[ShapEntry] = []
    peer_status_summary: dict = {}
    repeat_days_count: int = 0
    dispatch_action: Optional[str] = None
    dismiss_reason: Optional[str] = None
    resolved_at: Optional[str] = None
    resolver_id: Optional[str] = None
    feeder_id: Optional[str] = None

    class Config:
        from_attributes = True


class ActionRequest(BaseModel):
    action: Literal["DISPATCH_LINEMAN", "LOAD_BALANCE", "DISMISS"]
    reason_code: Optional[Literal["VACATION", "PLANNED_OUTAGE", "FALSE_POSITIVE", "OTHER"]] = None
    resolver_id: str


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _parse_alert_detail(alert: AlertEvent) -> AlertDetail:
    """Convert ORM model to AlertDetail, parsing JSON fields."""
    shap_top3 = []
    if alert.shap_top3:
        try:
            raw = json.loads(alert.shap_top3)
            shap_top3 = [ShapEntry(**item) for item in raw if isinstance(item, dict)]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    peer_status = {}
    if alert.peer_status_summary:
        try:
            peer_status = json.loads(alert.peer_status_summary)
        except (json.JSONDecodeError, TypeError):
            pass

    return AlertDetail(
        alert_id=alert.alert_id,
        meter_id=alert.meter_id,
        alert_type=alert.alert_type,
        state=alert.state,
        anomaly_confidence=alert.anomaly_confidence,
        pattern_type=alert.pattern_type,
        triggered_at=alert.triggered_at,
        pct_deviation_from_baseline=alert.pct_deviation_from_baseline,
        pct_deviation_from_peer_median=alert.pct_deviation_from_peer_median,
        pct_deviation_from_cluster_norm=alert.pct_deviation_from_cluster_norm,
        z_score=alert.z_score,
        shap_top3=shap_top3,
        peer_status_summary=peer_status,
        repeat_days_count=alert.repeat_days_count or 0,
        dispatch_action=alert.dispatch_action,
        dismiss_reason=alert.dismiss_reason,
        resolved_at=alert.resolved_at,
        resolver_id=alert.resolver_id,
        feeder_id=alert.feeder_id,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/alerts", response_model=List[AlertSummary])
async def list_alerts(
    state: Optional[str] = Query(None, description="Filter by state (NEW, WATCHING, etc.)"),
    alert_type: Optional[str] = Query(None, description="Filter by alert_type"),
    feeder_id: Optional[str] = Query(None, description="Filter by feeder_id"),
    db: AsyncSession = Depends(get_db),
) -> List[AlertSummary]:
    """
    Return all alerts from alert_events, filtered and sorted by anomaly_confidence DESC.
    """
    stmt = select(AlertEvent)
    if state:
        stmt = stmt.where(AlertEvent.state == state)
    if alert_type:
        stmt = stmt.where(AlertEvent.alert_type == alert_type)
    if feeder_id:
        stmt = stmt.where(AlertEvent.feeder_id == feeder_id)
    stmt = stmt.order_by(AlertEvent.anomaly_confidence.desc())

    result = await db.execute(stmt)
    alerts = result.scalars().all()
    return [AlertSummary.model_validate(a) for a in alerts]


@router.get("/alerts/{alert_id}", response_model=AlertDetail)
async def get_alert(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
) -> AlertDetail:
    """Return full alert detail including parsed shap_top3 and peer_status_summary."""
    result = await db.execute(
        select(AlertEvent).where(AlertEvent.alert_id == alert_id)
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found")
    return _parse_alert_detail(alert)


@router.patch("/alerts/{alert_id}/action", response_model=AlertDetail)
async def dispatch_action(
    alert_id: str,
    body: ActionRequest,
    db: AsyncSession = Depends(get_db),
) -> AlertDetail:
    """
    Apply a dispatch action to an alert.

    Business rules enforced:
    - DISMISS requires a non-empty reason_code (HTTP 422 if absent)
    - DISPATCHED/DISMISSED alerts cannot be acted on again (HTTP 409)
    - Writes to dispatch_audit_log
    """
    # Validate DISMISS requires reason_code
    if body.action == "DISMISS" and not body.reason_code:
        raise HTTPException(
            status_code=422,
            detail="reason_code is required when action is DISMISS",
        )

    result = await db.execute(
        select(AlertEvent).where(AlertEvent.alert_id == alert_id)
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found")

    # Terminal state check — dispatch action is final
    if alert.state in ("DISPATCHED", "DISMISSED"):
        raise HTTPException(
            status_code=409,
            detail=f"Alert '{alert_id}' is already in terminal state '{alert.state}'. "
                   "A new alert must be generated if the pattern re-emerges.",
        )

    # Apply state transition
    now_iso = datetime.now(timezone.utc).isoformat()
    new_state = "DISPATCHED" if body.action in ("DISPATCH_LINEMAN", "LOAD_BALANCE") else "DISMISSED"

    alert.state = new_state
    alert.dispatch_action = body.action
    alert.dismiss_reason = body.reason_code
    alert.resolved_at = now_iso
    alert.resolver_id = body.resolver_id

    # Write audit log entry
    audit = DispatchAuditLog(
        alert_id=alert_id,
        action=body.action,
        reason_code=body.reason_code,
        resolver_id=body.resolver_id,
        resolved_at=now_iso,
    )
    db.add(audit)
    await db.commit()
    await db.refresh(alert)

    return _parse_alert_detail(alert)
