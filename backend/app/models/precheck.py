"""Persisted history of pre-check runs.

Every time a user (or scheduled job) runs pre-checks against a device, a row
goes here. The `results` JSON keeps the per-check breakdown so the UI can
render the same view post-hoc, and the rolled-up counters make dashboard
queries cheap.

Bulk runs (kicked off via /api/devices/precheck/bulk) get a parent
`BulkPrecheckRun` row that groups N PrecheckRun children under one operation.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import Severity


class BulkPrecheckRun(Base):
    """A user-initiated bulk pre-check across N devices.

    Children PrecheckRun rows reference this via bulk_run_id. The UI watches
    one of these to show live progress while individual device runs complete.
    """

    __tablename__ = "bulk_precheck_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    # Total number of devices targeted; we know progress via COUNT(children).
    target_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # Snapshot of the requested check list (so re-runs of the same bulk op are reproducible).
    checks_requested: Mapped[list] = mapped_column(JSON, nullable=False)

    # Cancelled / completed flags; updated when worker tasks finish or user aborts.
    cancelled: Mapped[bool] = mapped_column(default=False, nullable=False)


class PrecheckRun(Base):
    __tablename__ = "precheck_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # If part of a bulk operation, points back to the parent so we can render
    # bulk progress cheaply.
    bulk_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("bulk_precheck_runs.id", ondelete="SET NULL"), index=True, nullable=True
    )

    ran_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )
    ran_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    # Worst severity across all checks — drives the row badge color.
    overall_severity: Mapped[Severity] = mapped_column(
        Enum(Severity, name="severity"), nullable=False
    )

    pass_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    warn_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fail_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skip_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # results: {check_name: {raw_state: bool, severity: str, reason: str, raw_reason: str}}
    results: Mapped[dict] = mapped_column(JSON, nullable=False)

    # If the run failed wholesale (couldn't connect, etc.) we still record a row
    # with overall_severity=FAIL and stash the message here.
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    device = relationship("Device", lazy="joined")
