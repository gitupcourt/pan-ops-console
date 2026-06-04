"""Upgrade jobs and per-device tasks.

An UpgradeJob is a user-initiated bulk upgrade across N devices. Each
device gets a DeviceUpgradeTask child that walks its own state machine.
HA peers share a `ha_pair_key` so the orchestrator can serialize the
secondary → failover → primary sequence within the pair while running
other pairs in parallel.

Per MIGRATION_NOTES §3.3: `task.progress` (the JSON column) is doing
real load-bearing work — completion markers in `progress.completed_phases`
are what makes Retry resume from the last incomplete step. Do NOT
refactor those into a separate table without preserving semantics.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db_types import py_enum_column
from app.db import Base
from app.upgrade.models.enums import JobState, TaskPhase, WorkflowType


class UpgradeJob(Base):
    __tablename__ = "upgrade_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_version: Mapped[str] = mapped_column(String(64), nullable=False)

    workflow: Mapped[WorkflowType] = mapped_column(
        py_enum_column(WorkflowType, name="workflow_type"),
        default=WorkflowType.FULL,
        nullable=False,
    )

    # Stage list when workflow == PARTIAL — JSON list of TaskPhase strings.
    workflow_stages: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    # If true, HA-paired upgrades pause for human OK after secondary
    # upgrade and after failover.
    require_failover_confirmation: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    require_primary_upgrade_confirmation: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    # If true, attempt to fail back to original primary after both
    # upgrades succeed.
    auto_failback: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    # If true, the orchestrator reboots each member automatically after
    # install. If false (default), it parks at AWAITING_REBOOT_CONFIRM
    # and the operator clicks "Reboot now" to proceed — gives a chance
    # to glance at the device before it actually drops mgmt plane.
    auto_reboot_after_install: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    # Pre-stage mode:
    #   "none"       — full upgrade (default).
    #   "stage_only" — run precheck + image download + pre-snapshot, then STOP
    #                  before install/reboot. For pre-staging a fleet ahead of
    #                  a maintenance window (the image download is the slow part;
    #                  caching it makes the later real upgrade fast).
    # A plain string (not a DB enum) so adding modes later (e.g. "hold") is a
    # no-op migration, not an ALTER TYPE.
    pre_stage_mode: Mapped[str] = mapped_column(
        String(16), default="none", server_default="none", nullable=False
    )

    # Pre-acknowledged overrides for the pre-check and post-check gates.
    # When True, a FAIL-severity result skips the AWAITING_*_OVERRIDE
    # park step and the orchestrator proceeds as if the operator had
    # clicked "Proceed anyway." Useful for fully-automated upgrades
    # where the operator reviewed acceptable failures up front.
    #
    # IMPORTANT: bypasses a human safety gate. Off by default.
    auto_ack_precheck_failures: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    auto_ack_postcheck_failures: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    # Either an uploaded image is referenced, or devices are told to
    # pull a version themselves from updates.paloaltonetworks.com.
    image_id: Mapped[int | None] = mapped_column(
        ForeignKey("panos_images.id"), nullable=True
    )
    image = relationship("PanosImage", lazy="joined")
    device_pull_image: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    # Which readiness-check preset to run for pre- and post-checks on
    # this job's devices. NULL = use whichever PrecheckSet has
    # `is_default=true` (or DEFAULT_READINESS_CHECKS if no set is
    # flagged default). ON DELETE SET NULL — if the operator deletes
    # a referenced set, the job falls back to the default rather
    # than failing.
    precheck_set_id: Mapped[int | None] = mapped_column(
        ForeignKey("precheck_sets.id", ondelete="SET NULL"), nullable=True
    )
    precheck_set = relationship("PrecheckSet", lazy="joined")

    state: Mapped[JobState] = mapped_column(
        py_enum_column(JobState, name="job_state"), default=JobState.PENDING, nullable=False
    )

    # Why the job went FAILED. Set by `_fail_job` (first-write-wins so a
    # cascade of downstream failures doesn't bury the root cause). The
    # per-task `error` covers in-phase failures; this also captures
    # orchestrator-level crashes / timeouts where no task is marked
    # FAILED, which otherwise leave the UI showing a bare "FAILED".
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tasks = relationship(
        "DeviceUpgradeTask",
        back_populates="job",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class DeviceUpgradeTask(Base):
    __tablename__ = "device_upgrade_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("upgrade_jobs.id"), index=True, nullable=False
    )
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id"), index=True, nullable=False
    )

    # Devices in the same HA pair share a key so the orchestrator can
    # serialize them. Standalone devices have a unique key (typically
    # str(device_id)).
    ha_pair_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    phase: Mapped[TaskPhase] = mapped_column(
        py_enum_column(TaskPhase, name="task_phase"),
        default=TaskPhase.PENDING,
        nullable=False,
    )

    # JSON storage for: precheck results, snapshot diff, postcheck
    # results, error info, completion markers, percent indicators, etc.
    # MIGRATION_NOTES §3.3: this is load-bearing state, not just
    # observability. The orchestrator's "Retry == resume" property
    # depends on `progress.completed_phases` markers.
    # Plain JSON column — NOT wrapped with MutableDict.as_mutable().
    # MutableDict was tried in PR #89 but caused crashes when any code
    # path committed between two dict mutations (e.g.
    # _phase_post_snapshot_and_diff: snapshot_svc.compare() commits
    # internally, expiring task.progress, then the next mutation hit
    # `InvalidRequestError: Can't flag attribute 'progress' modified;
    # it's not present in the object state`).
    #
    # Discipline for all callers: mutate via a fresh dict copy and
    # reassign — `progress = dict(task.progress or {})`,  then
    # `task.progress = progress` before commit. The new-reference
    # assignment is what SA's change detection actually needs; in-place
    # mutation of the loaded dict alone is not enough.
    progress: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # When phase is awaiting_*_confirm, this task blocks until a user
    # calls the confirm endpoint with the right token.
    confirmation_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    # Counts the times we've ticked the orchestrator for this task (debug).
    tick_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    job = relationship("UpgradeJob", back_populates="tasks", lazy="joined")
    device = relationship("Device", lazy="joined")
