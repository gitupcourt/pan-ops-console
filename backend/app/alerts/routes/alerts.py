"""Alerts query + state endpoints.

Phase 12a shipped read-only listing + summary. Phase 12b adds the
operator-action surface: acknowledge / unacknowledge an alert (silence
without resolving the underlying capacity issue).

Ack semantics: acknowledged_at is independent of cleared_at. An ack'd
alert that's still firing stays "active" for poll-loop evaluation (so
the alert row keeps updating its last_seen_at + pct snapshot), it
just doesn't draw the operator's eye on the home dashboard frame.
When the value finally drops and the alert clears, the ack stays
recorded as history.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.alerts.models.alert import Alert
from app.alerts.models.enums import AlertSeverity
from app.core.auth.deps import current_user
from app.core.auth.models.user import User
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
    # Username of the operator who ack'd; null when not acknowledged.
    # Surfaced from the users join so the UI can show "ack'd by alice"
    # without a separate /users fetch.
    acknowledged_by: str | None


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
    # Join devices for device_name. Outer-join users so the ack'd-by
    # username comes back in the same query — users.id may be NULL if
    # the alert isn't acknowledged yet, OR if the acking user was
    # later deleted (FK is SET NULL).
    stmt = (
        select(Alert, Device.name, User.username)
        .join(Device, Device.id == Alert.device_id)
        .join(User, User.id == Alert.acknowledged_by_user_id, isouter=True)
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
            acknowledged_by=ack_username,
        )
        for a, device_name, ack_username in rows
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


@router.post("/{alert_id}/acknowledge", response_model=AlertRead)
def acknowledge_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> AlertRead:
    """Silence an active alert without resolving the underlying issue.

    Idempotent — calling on an already-ack'd alert updates the ack to
    the current operator + now(). That's intentional: operators
    re-confirming acknowledgement (e.g. after a shift change) leaves a
    fresh trail in the timestamps.
    """
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    alert.acknowledged_at = datetime.now(timezone.utc)
    alert.acknowledged_by_user_id = user.id
    db.commit()
    return _alert_to_read(db, alert)


@router.post("/{alert_id}/unacknowledge", response_model=AlertRead)
def unacknowledge_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),  # noqa: ARG001 — auth-only
) -> AlertRead:
    """Clear the ack. Useful when an operator hits "ack" by mistake or
    wants to put a still-firing alert back into the active-attention
    bucket on the home dashboard."""
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    alert.acknowledged_at = None
    alert.acknowledged_by_user_id = None
    db.commit()
    return _alert_to_read(db, alert)


def _alert_to_read(db: Session, a: Alert) -> AlertRead:
    """Hydrate a single Alert row to the AlertRead shape used by the
    list endpoint. Used by the ack/unack endpoints which return the
    updated row to the caller.
    """
    device = db.get(Device, a.device_id)
    device_name = device.name if device else f"device#{a.device_id}"
    ack_user_name: str | None = None
    if a.acknowledged_by_user_id is not None:
        u = db.get(User, a.acknowledged_by_user_id)
        if u is not None:
            ack_user_name = u.username
    return AlertRead(
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
        acknowledged_by=ack_user_name,
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
