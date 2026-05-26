"""Alert rule read endpoints.

Phase 12a: read-only — list the configured rules so the /alerts page
can render the active threshold table. Phase 12b adds POST/PATCH/DELETE.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alerts.models.rule import AlertRule
from app.db import get_db

router = APIRouter(prefix="/alerts/rules", tags=["alerts"])


class AlertRuleRead(BaseModel):
    id: int
    name: str
    metric: str | None
    severity: str
    threshold_pct: int
    enabled: bool
    created_at: datetime
    updated_at: datetime


@router.get("", response_model=list[AlertRuleRead])
def list_rules(db: Session = Depends(get_db)) -> list[AlertRuleRead]:
    """All rules, with global defaults first (metric IS NULL), then
    per-metric overrides alphabetical. Disabled rules included so the
    UI can show them as toggled-off rather than hide them entirely.
    """
    rows = db.execute(
        select(AlertRule).order_by(
            # Global defaults (NULL metric) sort first via Postgres
            # NULLS FIRST semantics. SQLite treats NULL as the smallest
            # value with default ASC, so the same ordering falls out
            # without an explicit nulls clause.
            AlertRule.metric.asc(),
            AlertRule.severity.asc(),
        )
    ).scalars().all()
    return [
        AlertRuleRead(
            id=r.id,
            name=r.name,
            metric=r.metric,
            severity=r.severity.value,
            threshold_pct=r.threshold_pct,
            enabled=r.enabled,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]
