"""AlertRule — operator-configurable threshold definitions.

A rule says: "when a sample's % >= threshold_pct on a metric matching
this rule, fire an alert at this severity."

Rule scoping:
  - `metric IS NULL` — applies to every catalog metric. Two such rules
    are seeded by migration 0006 ("Default Warning" at 75 and "Default
    Critical" at 90) so an out-of-the-box install has alerts without
    operator effort.
  - `metric = "address_objects"` — overrides the default for that
    specific metric. Per-metric rules take precedence over the global
    default at the same severity.

Phase 12a ships read-only access; phase 12b adds the CRUD UI for
adjusting thresholds and disabling rules per the operator's playbook.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.alerts.models.enums import AlertSeverity
from app.core.db_types import py_enum_column
from app.db import Base


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Human-readable label. Required so the /alerts/rules UI has
    # something to show. Operator-editable.
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Null = applies to all metrics. Non-null = applies to that one
    # metric only (overrides the default at the same severity).
    metric: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    severity: Mapped[AlertSeverity] = mapped_column(
        py_enum_column(AlertSeverity, name="alert_severity"),
        nullable=False,
        index=True,
    )
    threshold_pct: Mapped[int] = mapped_column(Integer, nullable=False)
    # Consecutive samples that must breach the threshold before the alert
    # OPENS. 1 = instantaneous (right for slow-moving config counts). Higher
    # values suppress flapping on volatile metrics like CPU — e.g. 3 means
    # "fire only after CPU has been over the line for 3 polls in a row".
    # Maintaining/escalating an already-open alert is not re-gated.
    sustained_samples: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1", default=1
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # At most one rule per (metric, severity) combo. The "global"
        # default sits at metric=NULL — but NULL ≠ NULL in SQL UNIQUE
        # semantics, so two global rules at the same severity would
        # slip through this. We rely on the seeded migration + the
        # CRUD layer enforcing uniqueness at the API level.
        UniqueConstraint("metric", "severity", name="uq_alert_rules_metric_severity"),
    )
