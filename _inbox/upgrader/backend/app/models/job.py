"""Upgrade jobs and per-device tasks.

An UpgradeJob is a user-initiated bulk upgrade across N devices. Each device
gets a DeviceUpgradeTask child that walks its own state machine. HA peers share
a `ha_pair_key` so the orchestrator can serialize the secondary→failover→primary
sequence within the pair while running other pairs in parallel.
"""

from datetime import datetime
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import JobState, TaskPhase, WorkflowType


class UpgradeJob(Base):
    __tablename__ = "upgrade_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_version: Mapped[str] = mapped_column(String(64), nullable=False)

    workflow: Mapped[WorkflowType] = mapped_column(
        Enum(WorkflowType, name="workflow_type"), default=WorkflowType.FULL, nullable=False
    )

    # Stage list when workflow == PARTIAL — JSON list of TaskPhase strings
    workflow_stages: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    # If true, HA-paired upgrades pause for human OK after secondary upgrade & after failover
    require_failover_confirmation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    require_primary_upgrade_confirmation: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # If true, attempt to fail back to original primary after both upgrades succeed
    auto_failback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # If true, the orchestrator reboots each member automatically after install.
    # If false (default), it parks at AWAITING_REBOOT_CONFIRM and the operator
    # clicks "Reboot now" to proceed — gives a chance to glance at the device
    # before it actually drops mgmt plane.
    auto_reboot_after_install: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Pre-acknowledged overrides for the pre-check and post-check gates.
    # When True, a FAIL-severity result skips the AWAITING_*_OVERRIDE park
    # step and the orchestrator proceeds as if the operator had clicked
    # "Proceed anyway." Useful for fully-automated upgrades where the
    # operator has reviewed acceptable failures up front (e.g. known
    # benign content-version warnings on a remote lab).
    #
    # IMPORTANT: This bypasses a human safety gate. Off by default.
    auto_ack_precheck_failures: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_ack_postcheck_failures: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Either an uploaded image is referenced, or devices are told to pull a version themselves
    image_id: Mapped[int | None] = mapped_column(ForeignKey("panos_images.id"), nullable=True)
    image = relationship("PanosImage", lazy="joined")
    device_pull_image: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    state: Mapped[JobState] = mapped_column(
        Enum(JobState, name="job_state"), default=JobState.PENDING, nullable=False
    )

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tasks = relationship(
        "DeviceUpgradeTask",
        back_populates="job",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class DeviceUpgradeTask(Base):
    __tablename__ = "device_upgrade_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("upgrade_jobs.id"), index=True, nullable=False)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), index=True, nullable=False)

    # Devices in the same HA pair share a key so the orchestrator can serialize them.
    # Standalone devices have a unique key (typically str(device_id)).
    ha_pair_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    phase: Mapped[TaskPhase] = mapped_column(
        Enum(TaskPhase, name="task_phase"), default=TaskPhase.PENDING, nullable=False
    )

    # JSON storage for: precheck results, snapshot diff, postcheck results, error info, etc.
    progress: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # When phase is awaiting_*_confirm, this task blocks until a user calls the confirm endpoint.
    confirmation_token: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Counts the times we've ticked the orchestrator for this task (debug).
    tick_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    job = relationship("UpgradeJob", back_populates="tasks", lazy="joined")
    device = relationship("Device", lazy="joined")

    # Convenience pass-throughs so the JobOut schema can pick these up via
    # Pydantic's from_attributes without an extra DB round-trip.
    @property
    def device_name(self) -> str | None:
        return self.device.name if self.device else None

    @property
    def device_ha_role(self) -> str | None:
        return self.device.ha_role.value if self.device and self.device.ha_role else None

    @property
    def device_current_version(self) -> str | None:
        return self.device.current_version if self.device else None
