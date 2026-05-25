"""Upgrade job creation, listing, and human-confirmation gates."""

import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.db import get_db
from app.models.device import Device
from app.models.enums import JobState, TaskPhase
from app.models.job import DeviceUpgradeTask, UpgradeJob
from app.models.user import User
from app.schemas.job import JobCreate, JobOut

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _ha_pair_key(device: Device) -> str:
    """Stable key shared by both halves of an HA pair, or unique for standalone."""
    if device.ha_peer_id is None:
        return f"solo-{device.id}"
    a, b = sorted([device.id, device.ha_peer_id])
    return f"pair-{a}-{b}"


@router.get("", response_model=list[JobOut])
def list_jobs(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[UpgradeJob]:
    return db.query(UpgradeJob).order_by(UpgradeJob.created_at.desc()).all()


# Phases that mean "this device has work running or pending right now."
# Used by /active-tasks below. Excludes terminal (done/failed/aborted) and
# pending (work hasn't started yet — no value showing it as in-progress).
_ACTIVE_PHASES: set[TaskPhase] = {
    TaskPhase.PRECHECK,
    TaskPhase.AWAITING_PRECHECK_OVERRIDE,
    TaskPhase.SNAPSHOT,
    TaskPhase.DOWNLOADING_IMAGE,
    TaskPhase.SUSPEND_SECONDARY,
    TaskPhase.UPGRADE_SECONDARY,
    TaskPhase.AWAITING_REBOOT_CONFIRM,
    TaskPhase.POSTCHECK_SECONDARY,
    TaskPhase.AWAITING_POSTCHECK_OVERRIDE,
    TaskPhase.AWAITING_FAILOVER_CONFIRM,
    TaskPhase.FAILOVER,
    TaskPhase.AWAITING_PRIMARY_UPGRADE_CONFIRM,
    TaskPhase.UPGRADE_PRIMARY,
    TaskPhase.POSTCHECK_PRIMARY,
    TaskPhase.FAILBACK,
    TaskPhase.REPORT,
}


@router.get("/active-tasks")
def list_active_tasks(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[dict]:
    """Lightweight feed for the Devices table + Dashboard tile.

    Returns one entry per device that has a currently-active upgrade task,
    with just enough info to render a badge: which job, which phase, the
    download/install percent if any. Polled at ~5s on the Devices page so
    the badge animates without a full /api/jobs re-fetch.
    """
    rows = (
        db.query(DeviceUpgradeTask, UpgradeJob)
        .join(UpgradeJob, DeviceUpgradeTask.job_id == UpgradeJob.id)
        .filter(DeviceUpgradeTask.phase.in_(_ACTIVE_PHASES))
        .filter(UpgradeJob.state.in_([JobState.RUNNING, JobState.AWAITING_CONFIRMATION]))
        .all()
    )
    out: list[dict] = []
    for task, job in rows:
        prog = task.progress or {}
        out.append({
            "device_id": task.device_id,
            "task_id": task.id,
            "job_id": job.id,
            "job_name": job.name,
            "target_version": job.target_version,
            "phase": task.phase.value,
            "download_progress": prog.get("download_progress"),
            "install_progress": prog.get("install_progress"),
            "awaiting": task.phase.value.startswith("awaiting_"),
        })
    return out


@router.post("", response_model=JobOut, status_code=201)
def create_job(
    payload: JobCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UpgradeJob:
    devices = db.query(Device).filter(Device.id.in_(payload.device_ids)).all()
    if len(devices) != len(payload.device_ids):
        raise HTTPException(400, "One or more device_ids not found")

    job = UpgradeJob(
        name=payload.name,
        target_version=payload.target_version,
        workflow=payload.workflow,
        workflow_stages=payload.workflow_stages,
        require_failover_confirmation=payload.require_failover_confirmation,
        require_primary_upgrade_confirmation=payload.require_primary_upgrade_confirmation,
        auto_failback=payload.auto_failback,
        auto_reboot_after_install=payload.auto_reboot_after_install,
        auto_ack_precheck_failures=payload.auto_ack_precheck_failures,
        auto_ack_postcheck_failures=payload.auto_ack_postcheck_failures,
        image_id=payload.image_id,
        device_pull_image=payload.device_pull_image,
        created_by_id=user.id,
    )
    db.add(job)
    db.flush()  # get job.id

    for d in devices:
        task = DeviceUpgradeTask(
            job_id=job.id,
            device_id=d.id,
            ha_pair_key=_ha_pair_key(d),
            phase=TaskPhase.PENDING,
        )
        db.add(task)

    db.commit()
    db.refresh(job)

    # Hand the job to Celery. run_job will fan out one drive_pair per HA pair
    # (or per standalone device) and they execute in parallel up to worker
    # concurrency. We return immediately — the UI polls /api/jobs/{id} for
    # progress.
    from app.tasks.upgrade import run_job
    run_job.delay(job.id)

    return job


@router.get("/{job_id}", response_model=JobOut)
def get_job(
    job_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> UpgradeJob:
    job = db.get(UpgradeJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.post("/{job_id}/tasks/{task_id}/confirm", response_model=JobOut)
def confirm_task(
    job_id: int,
    task_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> UpgradeJob:
    """Release a task that's parked at an awaiting_*_confirm phase."""
    task = db.get(DeviceUpgradeTask, task_id)
    if not task or task.job_id != job_id:
        raise HTTPException(404, "Task not found")

    awaiting = {
        TaskPhase.AWAITING_FAILOVER_CONFIRM,
        TaskPhase.AWAITING_PRIMARY_UPGRADE_CONFIRM,
        TaskPhase.AWAITING_PRECHECK_OVERRIDE,
        TaskPhase.AWAITING_POSTCHECK_OVERRIDE,
        TaskPhase.AWAITING_REBOOT_CONFIRM,
    }
    if task.phase not in awaiting:
        raise HTTPException(409, f"Task is in phase {task.phase}, nothing to confirm")

    # The orchestrator polls task.confirmation_token while parked — setting
    # it here unblocks it on the next poll cycle (~5s).
    task.confirmation_token = secrets.token_urlsafe(16)
    db.commit()

    return db.get(UpgradeJob, job_id)


@router.post("/{job_id}/abort", response_model=JobOut)
def abort_job(
    job_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> UpgradeJob:
    job = db.get(UpgradeJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.state in (JobState.COMPLETED, JobState.FAILED, JobState.ABORTED):
        raise HTTPException(409, f"Job is already {job.state}")
    job.state = JobState.ABORTED
    db.commit()
    return job


@router.post("/{job_id}/retry", response_model=JobOut)
def retry_job(
    job_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> UpgradeJob:
    """Re-run a failed or aborted job from where it stopped.

    Resets every FAILED task in the job back to PENDING (clearing the
    error) and re-dispatches the orchestrator. Tasks that already reached
    DONE stay DONE. The phase functions are individually idempotent:
       - precheck always runs (re-validates current state)
       - snapshot always runs (fresh "before" snapshot)
       - download short-circuits if image is on disk
       - suspend / install / reboot short-circuit if device is at target
       - HA resume always runs (no-op on already-functional)
    So a retry naturally picks up wherever the orchestrator died.
    """
    job = db.get(UpgradeJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.state not in (JobState.FAILED, JobState.ABORTED):
        raise HTTPException(
            409,
            f"Can only retry FAILED or ABORTED jobs (this is {job.state.value}). "
            f"Use abort first if the job is stuck.",
        )

    # Reset the failed tasks. Leave DONE tasks alone — they're a known-good
    # checkpoint and the orchestrator's per-phase short-circuits will not
    # touch them on a re-run.
    for t in job.tasks:
        if t.phase == TaskPhase.FAILED:
            t.phase = TaskPhase.PENDING
            t.error = None
            # Keep progress.log so the user sees the history; the orchestrator
            # appends new lines as it goes.

    job.state = JobState.PENDING
    job.finished_at = None
    db.commit()

    from app.tasks.upgrade import run_job
    run_job.delay(job.id)
    return db.get(UpgradeJob, job_id)
