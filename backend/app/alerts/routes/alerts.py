"""Alert list endpoint — phase 8 scaffold; phase 12 fills it.

The home Dashboard's Active-alerts frame and the dedicated `/alerts`
page (also phase 12) both fetch from `GET /alerts`. Shipping the
empty-list scaffold now means the frontend's HomeDashboard frame in
phase 7 won't 404 against this route the moment it lands — it just
gets `[]` and renders the empty state cleanly.

When phase 12 lands the rule engine:
  - Adds `Alert` + `AlertRule` models + migration 0006.
  - Replaces this stub with a real query.
  - Adds POST /alerts/{id}/acknowledge, GET /alert-rules, etc.

The response shape is pinned now so the frontend can be written
against it confidently — see `AlertRead`.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/alerts", tags=["alerts"])


class AlertRead(BaseModel):
    """The shape phase 12's real query will return.

    Field reference:
      - severity: "warn" | "critical" — matches the per-rule threshold
        that fired.
      - alert_name: human-readable, e.g. "Approaching Max Capacity -
        Address Objects" (matches PA's catalog naming).
      - device_id / device_name: where to drill in.
      - metric: the catalog metric name (e.g. "address_objects") so
        clicking the alert can filter the Capacity table by
        (device, metric).
      - current_value / max_value / pct: snapshot at the time of fire.
      - first_seen_at: when the threshold was first crossed.
      - last_seen_at: when we most recently re-confirmed it crossed.
      - acknowledged_at: nullable; operator action.
    """

    id: int
    severity: str
    alert_name: str
    device_id: int
    device_name: str
    metric: str
    current_value: float | None
    max_value: float | None
    pct: float | None
    first_seen_at: datetime
    last_seen_at: datetime
    acknowledged_at: datetime | None


@router.get("", response_model=list[AlertRead])
def list_alerts() -> list[AlertRead]:
    """Active alerts, newest first. Empty list until phase 12 lands.

    Filters that the real impl will support (deferred to phase 12):
      - severity=warn|critical
      - acknowledged=true|false
      - device_id=...
      - metric=...
    """
    return []
