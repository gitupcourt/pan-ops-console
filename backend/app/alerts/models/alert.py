"""Alert — one row per (device, metric) currently or recently firing.

Lifecycle:
  - Threshold first crossed → INSERT with `first_seen_at = now`,
    `cleared_at = NULL`.
  - Subsequent polls re-confirm crossing → UPDATE `last_seen_at`,
    refresh `current_value`/`pct`. If severity changes (e.g. value
    climbs past the critical threshold), update `severity` in-place.
  - Value drops below threshold (with 5% hysteresis to prevent flap)
    → UPDATE `cleared_at = now`. The row stays in the table as
    history.
  - Threshold crossed again later → NEW row, because the previous
    one is now closed (`cleared_at IS NOT NULL`).

`threshold_pct` is snapshotted from the firing rule at fire time, so
an operator editing the rule later doesn't rewrite history.
Acknowledgement is tracked separately from clear — an operator can ack
an alert that's still active to silence it without resolving the
underlying capacity issue.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.alerts.models.enums import AlertSeverity
from app.core.db_types import py_enum_column
from app.db import Base


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    metric: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[AlertSeverity] = mapped_column(
        py_enum_column(AlertSeverity, name="alert_severity"),
        nullable=False,
        index=True,
    )
    # Snapshot of the rule's threshold at fire time. If the operator
    # edits the rule later, this stays — keeps the alert
    # self-describing.
    threshold_pct: Mapped[int] = mapped_column(Integer, nullable=False)
    current_value: Mapped[float] = mapped_column(Float, nullable=False)
    max_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # NULL = still active. Non-null = the value dropped below the
    # threshold (plus hysteresis) at the recorded time.
    cleared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # NULL = not yet acknowledged. Non-null = operator silenced this
    # alert at the recorded time. Independent of cleared_at — you can
    # ack a still-active alert.
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        # Fast lookup for "active alerts on this device/metric" — the
        # evaluator hits this on every poll for every sample.
        Index(
            "ix_alerts_device_metric_open",
            "device_id",
            "metric",
            "cleared_at",
        ),
        Index("ix_alerts_first_seen_at", "first_seen_at"),
    )
