"""Alerts query endpoints.

Phase 12a: read-only — list active alerts + summary counts for the
home dashboard frame. Phase 12b will add POST /alerts/{id}/acknowledge.

The response shape mirrors what the frontend was already coded
against during phase 8's scaffold (the previously-pinned `AlertRead`).
That contract is preserved so HomeDashboard's existing import doesn't
break — only the data source changes from `[]` to a real query.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.alerts.models.alert import Alert
from app.alerts.models.enums import AlertSeverity
from app.core.devices.models.device import Device
from app.db import get_db

router = APIRouter(prefix="/alerts", tags=["alerts"])


class AlertRead(BaseModel):
    id: int
    severity: str
    alert_name: str
    device_id: int
    device_name: str
    metric: str
    threshold_pct: int
    current_value: float | None
    max_value: float | None
    pct: float | None
    first_seen_at: datetime
    last_seen_at: datetime
    cleared_at: datetime | None
    acknowledged_at: datetime | None


class AlertsSummary(BaseModel):
    """Counts for the home dashboard's AlertsFrame."""

    critical: int
    warning: int
    acknowledged: int  # subset of (critical + warning) that's been ack'd
    total_active: int  # everything where cleared_at IS NULL


@router.get("", response_model=list[AlertRead])
def list_alerts(
    db: Session = Depends(get_db),
    state: Literal["active", "all"] = Query(
        "active", description="active = open (cleared_at IS NULL); all = include history"
    ),
    severity: AlertSeverity | None = Query(None),
    device_id: int | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
) -> list[AlertRead]:
    """Newest-first list of alerts.

    `state=active` (default) returns only currently-firing alerts —
    that's what the /alerts page and the home dashboard frame both
    show. `state=all` includes cleared history for the (eventual)
    audit/timeline view.
    """
    # Join devices so we can return device_name without a per-row
    # lookup, matching the AlertRead contract.
    stmt = (
        select(Alert, Device.name)
        .join(Device, Device.id == Alert.device_id)
        .order_by(Alert.last_seen_at.desc())
        .limit(limit)
    )
    if state == "active":
        stmt = stmt.where(Alert.cleared_at.is_(None))
    if severity is not None:
        stmt = stmt.where(Alert.severity == severity)
    if device_id is not None:
        stmt = stmt.where(Alert.device_id == device_id)

    rows = db.execute(stmt).all()
    return [
        AlertRead(
            id=a.id,
            severity=a.severity.value,
            alert_name=_alert_name(a),
            device_id=a.device_id,
            device_name=device_name,
            metric=a.metric,
            threshold_pct=a.threshold_pct,
            current_value=a.current_value,
            max_value=a.max_value,
            pct=a.pct,
            first_seen_at=a.first_seen_at,
            last_seen_at=a.last_seen_at,
            cleared_at=a.cleared_at,
            acknowledged_at=a.acknowledged_at,
        )
        for a, device_name in rows
    ]


@router.get("/summary", response_model=AlertsSummary)
def alerts_summary(db: Session = Depends(get_db)) -> AlertsSummary:
    """Counts for the home dashboard's AlertsFrame.

    Single roundtrip — grouped count over open alerts, plus a separate
    count for "open and acknowledged."
    """
    by_sev = dict(
        db.execute(
            select(Alert.severity, func.count(Alert.id))
            .where(Alert.cleared_at.is_(None))
            .group_by(Alert.severity)
        ).all()
    )
    critical = by_sev.get(AlertSeverity.CRITICAL, 0)
    warning = by_sev.get(AlertSeverity.WARNING, 0)
    acknowledged = db.execute(
        select(func.count(Alert.id)).where(
            and_(Alert.cleared_at.is_(None), Alert.acknowledged_at.is_not(None))
        )
    ).scalar_one()
    return AlertsSummary(
        critical=critical,
        warning=warning,
        acknowledged=acknowledged,
        total_active=critical + warning,
    )


def _alert_name(a: Alert) -> str:
    """Human-readable label following PA's catalog naming convention.

    The metric is shown raw here — the frontend has the catalog and
    can render the friendly description (e.g. "Address objects") in
    the table cell.
    """
    if a.severity == AlertSeverity.CRITICAL:
        return f"Critical Capacity - {a.metric}"
    return f"Approaching Max Capacity - {a.metric}"
