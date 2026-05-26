"""Pydantic request / response schemas for the upgrade module's HTTP surface.

Schemas live here rather than colocated with routes so the Celery task
module (phase 4d) can import the same shapes when it serializes a task
payload or emits a progress event. Mirrors how `capacity/schemas.py`
and `core/devices/schemas.py` are organized.

Notable choices baked in:

- **Job creation accepts `device_ids` directly**, not a "build a group
  first then attach it." The grouping logic ("upgrade everything tagged
  X") belongs in the UI layer; the API stays declarative — the operator
  hands us a list, we create one task per device. HA peer pairing is
  derived automatically by the orchestrator from `device.ha_peer_id`,
  so callers don't pre-shape pairs.

- **Image is either `image_id` (uploaded blob) OR `device_pull_image`
  (let each device fetch from updates.paloaltonetworks.com).** Validator
  rejects both-set / both-unset.

- **Confirmation / override endpoints take a `token`** that must match
  `DeviceUpgradeTask.confirmation_token`. Generated server-side at the
  point the orchestrator parks the task; surfaced to the UI via the
  task detail. Operators can't forge an early confirm because the token
  isn't valid until the task is parked at the matching phase.

- **TaskRead does NOT inline `progress` JSON.** That blob can be tens
  of KB (precheck results, snapshot summaries). It's fetched separately
  via the per-task detail route to keep list/detail responses small.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.upgrade.models.enums import JobState, TaskPhase, WorkflowType


# ---------- Images ----------


class ImageRead(BaseModel):
    id: int
    version: str
    filename: str | None
    sha256: str | None
    size_bytes: int | None
    notes: str | None
    created_at: datetime
    # True if the image was uploaded (has a local file backing it),
    # False if it's just a version reference for devices to pull
    # themselves from updates.paloaltonetworks.com.
    uploaded: bool

    model_config = {"from_attributes": True}


class ImageCreate(BaseModel):
    """Register an image by version only — no upload in this PR.

    Operators register the version string ("11.1.4-h7") and tell their
    devices to pull from updates.paloaltonetworks.com. Image upload
    (multipart streaming + local-disk storage) lands as follow-up work;
    the route layer already differentiates uploaded vs version-only via
    `filename` being null.
    """

    version: str = Field(..., min_length=1, max_length=64)
    notes: str | None = Field(default=None, max_length=500)


# ---------- Job tasks ----------


class TaskRead(BaseModel):
    """Compact per-device task view, embedded in JobDetail.

    Excludes `progress` JSON blob — fetch via /upgrade/tasks/{id} when
    the UI wants the full breakdown.
    """

    id: int
    job_id: int
    device_id: int
    device_name: str  # convenience denorm so UI doesn't need a second hop
    ha_pair_key: str
    phase: TaskPhase
    error: str | None
    confirmation_token: str | None
    tick_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskDetail(TaskRead):
    """Full task detail including the load-bearing `progress` JSON.

    Per MIGRATION_NOTES §3.3 the `progress.completed_phases` markers
    drive the orchestrator's resume-on-retry semantics — render-only
    on the UI side, never mutate.
    """

    progress: dict | None


class TaskConfirm(BaseModel):
    """Body for /upgrade/tasks/{id}/confirm.

    Operator advances a parked task (reboot/failover/primary-upgrade
    confirmation gates). Token must match the server-issued
    `confirmation_token` on the task at the time of parking.
    """

    token: str = Field(..., min_length=1, max_length=64)


class TaskOverride(BaseModel):
    """Body for /upgrade/tasks/{id}/override.

    Operator proceeds past a precheck/postcheck FAIL severity by
    explicitly acknowledging the failures. Token mechanism matches
    TaskConfirm.
    """

    token: str = Field(..., min_length=1, max_length=64)


# ---------- Jobs ----------


class JobCreate(BaseModel):
    """Operator hands us a list of device IDs and a target version.

    The orchestrator derives HA pair grouping from `device.ha_peer_id`
    at start time; callers don't need to shape pairs themselves.
    """

    name: str = Field(..., min_length=1, max_length=255)
    target_version: str = Field(..., min_length=1, max_length=64)
    device_ids: list[int] = Field(..., min_length=1)

    workflow: WorkflowType = WorkflowType.FULL
    # Required iff workflow == PARTIAL. Each entry is a TaskPhase
    # value the operator wants the orchestrator to execute (others
    # are skipped). Validator enforces.
    workflow_stages: list[str] | None = None

    image_id: int | None = None
    device_pull_image: bool = False

    require_failover_confirmation: bool = True
    require_primary_upgrade_confirmation: bool = False
    auto_failback: bool = False
    auto_reboot_after_install: bool = False

    # Pre-acknowledge ALL precheck/postcheck FAIL severities — used when
    # the operator has reviewed acceptable failures up front and wants a
    # fully automated run. Bypasses a human safety gate; off by default.
    auto_ack_precheck_failures: bool = False
    auto_ack_postcheck_failures: bool = False

    @model_validator(mode="after")
    def _validate_image_source(self) -> "JobCreate":
        # Exactly one of image_id / device_pull_image must be set.
        # Both-unset = no way to install anything; both-set = ambiguous.
        if (self.image_id is None) and (not self.device_pull_image):
            raise ValueError(
                "Either image_id or device_pull_image must be set "
                "(otherwise no install image is specified)"
            )
        if (self.image_id is not None) and self.device_pull_image:
            raise ValueError(
                "Cannot set both image_id and device_pull_image — pick one"
            )
        return self

    @model_validator(mode="after")
    def _validate_workflow_stages(self) -> "JobCreate":
        if self.workflow == WorkflowType.PARTIAL:
            if not self.workflow_stages:
                raise ValueError(
                    "workflow_stages required when workflow=PARTIAL"
                )
            # Each entry must match a TaskPhase value to be meaningful
            # to the orchestrator.
            valid = {p.value for p in TaskPhase}
            invalid = [s for s in self.workflow_stages if s not in valid]
            if invalid:
                raise ValueError(
                    f"unknown workflow_stages: {invalid}. "
                    f"Valid values: {sorted(valid)}"
                )
        elif self.workflow_stages:
            raise ValueError(
                "workflow_stages only valid when workflow=PARTIAL"
            )
        return self


class JobRead(BaseModel):
    """List view of an UpgradeJob — no tasks embedded."""

    id: int
    name: str
    target_version: str
    workflow: WorkflowType
    state: JobState
    task_count: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class JobDetail(JobRead):
    """Full job detail — includes tasks (compact form) and config flags.

    Operators land on this view from the job list and use it to
    monitor progress, confirm at park steps, retry failed tasks.
    """

    workflow_stages: list[str] | None
    image_id: int | None
    device_pull_image: bool

    require_failover_confirmation: bool
    require_primary_upgrade_confirmation: bool
    auto_failback: bool
    auto_reboot_after_install: bool
    auto_ack_precheck_failures: bool
    auto_ack_postcheck_failures: bool

    tasks: list[TaskRead]

    model_config = {"from_attributes": True}
