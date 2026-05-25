from datetime import datetime
from pydantic import BaseModel

from app.models.enums import JobState, TaskPhase, WorkflowType


class JobCreate(BaseModel):
    name: str
    target_version: str
    device_ids: list[int]
    workflow: WorkflowType = WorkflowType.FULL
    workflow_stages: list[str] | None = None
    require_failover_confirmation: bool = True
    require_primary_upgrade_confirmation: bool = False
    auto_failback: bool = False
    auto_reboot_after_install: bool = False
    auto_ack_precheck_failures: bool = False
    auto_ack_postcheck_failures: bool = False
    image_id: int | None = None
    device_pull_image: bool = False


class TaskOut(BaseModel):
    id: int
    device_id: int
    ha_pair_key: str
    phase: TaskPhase
    progress: dict | None
    error: str | None
    updated_at: datetime
    # Convenience fields denormalized from the joined Device so the UI can
    # render the job-detail timeline without an N+1 fetch.
    device_name: str | None = None
    device_ha_role: str | None = None
    device_current_version: str | None = None

    class Config:
        from_attributes = True


class JobOut(BaseModel):
    id: int
    name: str
    target_version: str
    workflow: WorkflowType
    state: JobState
    require_failover_confirmation: bool
    require_primary_upgrade_confirmation: bool
    auto_failback: bool
    auto_reboot_after_install: bool
    auto_ack_precheck_failures: bool
    auto_ack_postcheck_failures: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    tasks: list[TaskOut] = []

    class Config:
        from_attributes = True
