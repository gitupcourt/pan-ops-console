"""Pre-stage operations: download a PAN-OS image to a device without installing it.

Mirrors the bulk-precheck shape: a parent BulkStageRun row groups N child
DeviceStageRun rows so the UI can poll one endpoint for live progress.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import Severity  # reuse pass/warn/fail/skip for per-device outcome


class BulkStageRun(Base):
    __tablename__ = "bulk_stage_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    target_count: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class DeviceStageRun(Base):
    """Per-device record of a stage attempt (success or failure)."""

    __tablename__ = "device_stage_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    bulk_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("bulk_stage_runs.id", ondelete="SET NULL"), index=True, nullable=True
    )
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # SUCCESS = downloaded; FAIL = errored; PASS = already-downloaded (no-op);
    # SKIP = unused for staging.
    outcome: Mapped[Severity] = mapped_column(
        Enum(Severity, name="severity"), nullable=False, default=Severity.FAIL
    )
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    device = relationship("Device", lazy="joined")
