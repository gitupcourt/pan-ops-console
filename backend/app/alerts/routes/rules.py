"""Alert rule CRUD endpoints.

Phase 12a: read-only. Phase 12b: full CRUD so operators can edit
thresholds, disable noisy rules, and add per-metric overrides without
direct DB access.

Uniqueness contract: at most one rule per (metric, severity) pair.
Enforced both at the DB level (uq_alert_rules_metric_severity) and
via friendly 409 responses here so the UI can surface the conflict
without raising a generic 500.

`metric IS NULL` means "applies to all metrics" — the global default.
Operators can have one global Warning + one global Critical + any
number of per-metric overrides.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.alerts.models.enums import AlertSeverity
from app.alerts.models.rule import AlertRule
from app.db import get_db

router = APIRouter(prefix="/alerts/rules", tags=["alerts"])


class AlertRuleRead(BaseModel):
    id: int
    name: str
    metric: str | None
    severity: str
    threshold_pct: int
    # Consecutive breaching samples required to OPEN the alert. 1 = instant.
    sustained_samples: int
    enabled: bool
    created_at: datetime
    updated_at: datetime


class AlertRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    # null = global default that applies to every metric. Non-null =
    # per-metric override (matches the metric name in the catalog).
    metric: str | None = Field(default=None, max_length=64)
    severity: AlertSeverity
    threshold_pct: int = Field(ge=1, le=100)
    # 1 = fire the instant the threshold is breached (good for slow-moving
    # config counts). Higher = require N consecutive breaching polls before
    # firing (good for volatile metrics like CPU).
    sustained_samples: int = Field(default=1, ge=1, le=20)
    enabled: bool = True


class AlertRuleUpdate(BaseModel):
    """Partial update — only the fields the operator sent. metric and
    severity are intentionally NOT updatable, because changing them
    would violate the uniqueness contract or orphan the rule's
    historical alerts in a confusing way. To "change" metric/severity,
    delete + recreate."""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    threshold_pct: int | None = Field(default=None, ge=1, le=100)
    sustained_samples: int | None = Field(default=None, ge=1, le=20)
    enabled: bool | None = None


@router.get("", response_model=list[AlertRuleRead])
def list_rules(db: Session = Depends(get_db)) -> list[AlertRuleRead]:
    """All rules, global defaults first (metric IS NULL), then
    per-metric overrides alphabetical. Disabled rules included so the
    UI can show them as toggled-off rather than hide them entirely.
    """
    rows = db.execute(
        select(AlertRule).order_by(AlertRule.metric.asc(), AlertRule.severity.asc())
    ).scalars().all()
    return [_to_read(r) for r in rows]


@router.post("", response_model=AlertRuleRead, status_code=201)
def create_rule(
    body: AlertRuleCreate,
    db: Session = Depends(get_db),
) -> AlertRuleRead:
    """Add a new rule. 409 if (metric, severity) already exists.

    To add a per-metric override (e.g. tighter threshold on
    address_objects than the global default), POST with metric set
    and a higher/lower threshold_pct than the global rule's.
    """
    rule = AlertRule(
        name=body.name,
        metric=body.metric,
        severity=body.severity,
        threshold_pct=body.threshold_pct,
        sustained_samples=body.sustained_samples,
        enabled=body.enabled,
    )
    db.add(rule)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                f"A rule already exists for metric={body.metric!r} "
                f"severity={body.severity.value}. Edit that rule instead, "
                "or delete it first."
            ),
        ) from exc
    db.refresh(rule)
    return _to_read(rule)


@router.patch("/{rule_id}", response_model=AlertRuleRead)
def update_rule(
    rule_id: int,
    body: AlertRuleUpdate,
    db: Session = Depends(get_db),
) -> AlertRuleRead:
    """Partial update. Send only the fields you want to change.

    metric + severity are immutable per the contract documented on
    AlertRuleUpdate — delete + recreate if you need to change them.
    """
    rule = db.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    if body.name is not None:
        rule.name = body.name
    if body.threshold_pct is not None:
        rule.threshold_pct = body.threshold_pct
    if body.sustained_samples is not None:
        rule.sustained_samples = body.sustained_samples
    if body.enabled is not None:
        rule.enabled = body.enabled
    db.commit()
    db.refresh(rule)
    return _to_read(rule)


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    """Hard delete. Historical alert rows are not affected — they
    snapshotted threshold_pct at fire time, so they remain
    self-describing even with the rule gone.

    Operators can re-add a deleted default rule by POSTing the same
    shape.

    Intentionally no `-> None` annotation: FastAPI's newer behaviour
    is to treat any non-Response return annotation (including `None`)
    as declaring a response model, which conflicts with the 204
    status-code-must-not-have-body rule and raises an assertion at
    app startup. Matches the convention in the existing DELETE
    handlers under core/auth/, core/devices/, core/panorama/.
    """
    rule = db.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="rule not found")
    db.delete(rule)
    db.commit()


def _to_read(r: AlertRule) -> AlertRuleRead:
    return AlertRuleRead(
        id=r.id,
        name=r.name,
        metric=r.metric,
        severity=r.severity.value,
        threshold_pct=r.threshold_pct,
        sustained_samples=r.sustained_samples,
        enabled=r.enabled,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )
