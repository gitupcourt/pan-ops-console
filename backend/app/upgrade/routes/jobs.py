"""HTTP surface for upgrade jobs + per-device tasks.

Operator workflow this serves:

  1. POST /upgrade/jobs                — create a job (state=PENDING).
                                         Server creates one DeviceUpgradeTask
                                         per requested device, derives
                                         ha_pair_key from device.ha_peer_id.
  2. POST /upgrade/jobs/{id}/start     — transition PENDING → RUNNING,
                                         set started_at, and (in phase 4d)
                                         enqueue Celery `upgrade.drive_pair`
                                         tasks per ha_pair_key. Until 4d
                                         lands the state changes here are
                                         a no-op for the orchestrator
                                         (see comment in start_job).
  3. GET  /upgrade/jobs                — list jobs (compact view).
  4. GET  /upgrade/jobs/{id}           — detail including embedded tasks.
  5. POST /upgrade/jobs/{id}/abort     — operator pulls the cord. Tasks
                                         in flight are not interrupted
                                         mid-API-call, but the
                                         orchestrator checks job.state on
                                         every tick and stops advancing.
  6. DELETE /upgrade/jobs/{id}         — remove a finished or aborted job
                                         and cascade its tasks/snapshots.
                                         Refuses on RUNNING jobs to avoid
                                         deleting in-flight state.

Per-task ops (operator clicks "Confirm" / "Override" / "Retry" on a
parked or failed task):

  - POST /upgrade/tasks/{id}/confirm   — token-gated advance past
                                         AWAITING_REBOOT_CONFIRM,
                                         AWAITING_FAILOVER_CONFIRM,
                                         AWAITING_PRIMARY_UPGRADE_CONFIRM.
  - POST /upgrade/tasks/{id}/override  — token-gated advance past
                                         AWAITING_PRECHECK_OVERRIDE,
                                         AWAITING_POSTCHECK_OVERRIDE.
  - POST /upgrade/tasks/{id}/retry     — re-run from last incomplete
                                         marker (MIGRATION_NOTES §3.3:
                                         the orchestrator's
                                         `reconcile_markers_with_device_state`
                                         handles drift before resuming).

Celery dispatch (phase 4d): `start_job`, `confirm_task`,
`override_task`, and `retry_task` all hand the orchestrator one
`drive_pair_task.delay(job_id, ha_pair_key)` per affected pair. The
orchestrator runs to the next park point, returns, and waits for the
next dispatch — re-entry resumes from the last completed phase marker
in `task.progress.completed_phases`.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session
from sqlalchemy.orm.attributes import flag_modified

from app.core.auth.deps import current_user
from app.core.auth.models.user import User
from app.core.devices.models.device import Device
from app.db import get_db
from app.upgrade.models.enums import JobState, TaskPhase
from app.upgrade.models.job import DeviceUpgradeTask, UpgradeJob
from app.upgrade.schemas import (
    JobCreate,
    JobDetail,
    JobRead,
    TaskDetail,
    TaskRead,
)
from app.upgrade.tasks import drive_pair_task

router = APIRouter(tags=["upgrade"])


# ---------- helpers ----------


def _ha_pair_key_for(device: Device) -> str:
    """Group key the orchestrator uses to serialize HA-paired upgrades.

    Devices with no peer get a unique key (their own id); paired devices
    get a stable key derived from the LOWER of the two device ids, so
    both members of a pair land in the same group regardless of which
    one we encounter first.
    """
    if device.ha_peer_id is None:
        return str(device.id)
    return f"pair-{min(device.id, device.ha_peer_id)}"


def _device_display_name(d: Device) -> str:
    return d.name or d.hostname or f"device-{d.id}"


def _job_to_read(job: UpgradeJob, task_count: int) -> JobRead:
    return JobRead.model_validate(
        {
            "id": job.id,
            "name": job.name,
            "target_version": job.target_version,
            "workflow": job.workflow,
            "state": job.state,
            "task_count": task_count,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
        }
    )


def _task_to_read(task: DeviceUpgradeTask) -> TaskRead:
    from app.upgrade.models.precheck import PrecheckRun  # local — avoid cycle

    # Find the latest PrecheckRun for this task's device. We pick by
    # ran_at desc; with realistic job sizes (~5-20 tasks) this is one
    # cheap indexed query per task per render. If JobDetail starts
    # showing more than a few jobs at once we can batch this with a
    # single grouped query.
    db = object_session(task)
    precheck_payload = None
    if db is not None:
        latest = (
            db.query(PrecheckRun)
            .filter(PrecheckRun.device_id == task.device_id)
            .order_by(PrecheckRun.ran_at.desc())
            .first()
        )
        if latest is not None:
            precheck_payload = _precheck_to_summary(latest)

    # Snapshot IDs are stashed in progress by the orchestrator
    # (_phase_snapshot / _phase_post_snapshot_and_diff). Lift them to
    # top-level fields on TaskRead so the UI doesn't have to scrape
    # progress to know what's clickable.
    progress = task.progress or {}
    failing_areas_raw = progress.get("snapshot_diff_failing_areas")
    if isinstance(failing_areas_raw, str):
        failing_areas = [a.strip() for a in failing_areas_raw.split(",") if a.strip()]
    elif isinstance(failing_areas_raw, list):
        failing_areas = [str(a) for a in failing_areas_raw]
    else:
        failing_areas = None

    return TaskRead.model_validate(
        {
            "id": task.id,
            "job_id": task.job_id,
            "device_id": task.device_id,
            "device_name": _device_display_name(task.device),
            "ha_pair_key": task.ha_pair_key,
            "phase": task.phase,
            "error": task.error,
            "confirmation_token": task.confirmation_token,
            "tick_count": task.tick_count,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "progress": task.progress,
            "precheck": precheck_payload,
            "pre_snapshot_id": progress.get("pre_snapshot_id"),
            "post_snapshot_id": progress.get("post_snapshot_id"),
            "snapshot_diff_id": progress.get("snapshot_diff_id"),
            "snapshot_diff_all_passed": progress.get("snapshot_diff_all_passed"),
            "snapshot_diff_failing_areas": failing_areas,
        }
    )


def _precheck_to_summary(run) -> dict:
    """Normalize a PrecheckRun ORM row into the API summary shape.

    `results` on the row is whatever the orchestrator + classifier
    persisted. Shape per check is {state, reason, severity} keyed by
    check name. We unwrap into a list sorted severity-desc then
    name-asc so the UI lands the operator's eye on FAIL first.
    """
    raw = run.results or {}
    severity_order = {"fail": 0, "warn": 1, "skip": 2, "pass": 3}
    checks = [
        {
            "name": name,
            "state": bool(payload.get("state")),
            "reason": str(payload.get("reason") or ""),
            "severity": str(payload.get("severity") or "pass"),
        }
        for name, payload in raw.items()
    ]
    checks.sort(key=lambda c: (severity_order.get(c["severity"], 99), c["name"]))
    return {
        "id": run.id,
        "ran_at": run.ran_at,
        "overall_severity": run.overall_severity.value
        if hasattr(run.overall_severity, "value")
        else str(run.overall_severity),
        "pass_count": run.pass_count,
        "warn_count": run.warn_count,
        "fail_count": run.fail_count,
        "skip_count": run.skip_count,
        "checks": checks,
        "error": run.error,
    }


def _job_to_detail(job: UpgradeJob) -> JobDetail:
    return JobDetail.model_validate(
        {
            "id": job.id,
            "name": job.name,
            "target_version": job.target_version,
            "workflow": job.workflow,
            "state": job.state,
            "task_count": len(job.tasks),
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "workflow_stages": job.workflow_stages,
            "image_id": job.image_id,
            "device_pull_image": job.device_pull_image,
            "require_failover_confirmation": job.require_failover_confirmation,
            "require_primary_upgrade_confirmation": (
                job.require_primary_upgrade_confirmation
            ),
            "auto_failback": job.auto_failback,
            "auto_reboot_after_install": job.auto_reboot_after_install,
            "auto_ack_precheck_failures": job.auto_ack_precheck_failures,
            "auto_ack_postcheck_failures": job.auto_ack_postcheck_failures,
            "tasks": [_task_to_read(t) for t in job.tasks],
        }
    )


# ---------- /upgrade/jobs ----------


@router.get("/upgrade/jobs/summary")
def jobs_summary(db: Session = Depends(get_db)) -> dict[str, int]:
    """Counts of upgrade jobs by state — powers the home dashboard's
    Upgrades frame and any "X jobs in flight" affordance elsewhere.

    Returns a flat dict keyed by JobState.value with the count. States
    with zero jobs are still present in the response (with 0) so the
    frontend can render the tile set without conditional logic.
    """
    counts = dict(
        db.execute(
            select(UpgradeJob.state, func.count(UpgradeJob.id))
            .group_by(UpgradeJob.state)
        ).all()
    )
    # Initialize every JobState to 0 so the response is shape-stable
    # regardless of which states currently have rows.
    out: dict[str, int] = {state.value: 0 for state in JobState}
    for state, count in counts.items():
        out[state.value] = count
    return out


@router.get("/upgrade/jobs", response_model=list[JobRead])
def list_jobs(db: Session = Depends(get_db)):
    """List jobs newest first.

    Includes a `task_count` aggregate so the UI doesn't have to
    fetch each job's detail to render a "3 of 12 done" badge — the
    actual per-task state breakdown is on the detail endpoint.
    """
    # Aggregate task counts in one query so list view stays fast even
    # with many jobs × many tasks.
    counts = dict(
        db.execute(
            select(DeviceUpgradeTask.job_id, func.count(DeviceUpgradeTask.id))
            .group_by(DeviceUpgradeTask.job_id)
        ).all()
    )
    jobs = (
        db.execute(
            select(UpgradeJob)
            # Newest first by wall clock, with id as a tiebreaker so jobs
            # created within the same second (common in tests + bulk
            # operator workflows) get a stable order. Without this SQLite
            # falls back to row-insertion order, which is fine in prod
            # but flaky in tests and surprising in the UI.
            .order_by(UpgradeJob.created_at.desc(), UpgradeJob.id.desc())
        )
        .scalars()
        .all()
    )
    return [_job_to_read(j, counts.get(j.id, 0)) for j in jobs]


@router.post(
    "/upgrade/jobs",
    response_model=JobDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_job(
    payload: JobCreate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Create a job in PENDING state with one task per requested device.

    HA pair grouping is derived from `device.ha_peer_id` server-side —
    callers do not pre-shape pairs. The orchestrator will serialize
    upgrades within a pair (secondary → failover → primary) at run time;
    pairs run in parallel with other pairs.

    Image source validation:
      - `image_id` set: that PanosImage must exist.
      - `device_pull_image=True`: no further validation — devices pull
        from updates.paloaltonetworks.com at orchestration time.

    Device validation:
      - Every device_id must exist.
      - We do NOT require devices to currently be `connected` per
        Panorama — the orchestrator's precheck will surface unreachable
        devices on a per-device basis, which preserves partial progress
        on partial-online fleets.
    """
    # Lookup devices in one query. Missing ones produce a 400 with the
    # specific IDs that don't exist so the operator can fix their input.
    devices = (
        db.execute(select(Device).where(Device.id.in_(payload.device_ids)))
        .scalars()
        .all()
    )
    found_ids = {d.id for d in devices}
    missing = sorted(set(payload.device_ids) - found_ids)
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"device_ids not found: {missing}",
        )

    if payload.image_id is not None:
        from app.upgrade.models.image import PanosImage  # local — avoid cycle

        img = db.get(PanosImage, payload.image_id)
        if img is None:
            raise HTTPException(
                status_code=400,
                detail=f"image_id {payload.image_id} not found",
            )

    # Validate precheck_set_id if supplied — same pattern as image_id.
    if payload.precheck_set_id is not None:
        from app.upgrade.models.precheck_set import PrecheckSet  # local — avoid cycle

        ps = db.get(PrecheckSet, payload.precheck_set_id)
        if ps is None:
            raise HTTPException(
                status_code=400,
                detail=f"precheck_set_id {payload.precheck_set_id} not found",
            )

    job = UpgradeJob(
        name=payload.name,
        target_version=payload.target_version,
        workflow=payload.workflow,
        workflow_stages=payload.workflow_stages,
        image_id=payload.image_id,
        device_pull_image=payload.device_pull_image,
        require_failover_confirmation=payload.require_failover_confirmation,
        require_primary_upgrade_confirmation=(
            payload.require_primary_upgrade_confirmation
        ),
        auto_failback=payload.auto_failback,
        auto_reboot_after_install=payload.auto_reboot_after_install,
        auto_ack_precheck_failures=payload.auto_ack_precheck_failures,
        auto_ack_postcheck_failures=payload.auto_ack_postcheck_failures,
        precheck_set_id=payload.precheck_set_id,
        state=JobState.PENDING,
        created_by_id=user.id,
    )
    db.add(job)
    db.flush()  # get job.id before creating child tasks

    # Devices in stable order — by id — so paired devices land
    # adjacent in the task list (cosmetic) and tests are deterministic.
    devices_sorted = sorted(devices, key=lambda d: d.id)
    for device in devices_sorted:
        task = DeviceUpgradeTask(
            job_id=job.id,
            device_id=device.id,
            ha_pair_key=_ha_pair_key_for(device),
            phase=TaskPhase.PENDING,
            progress={"completed_phases": []},
        )
        db.add(task)

    db.commit()
    db.refresh(job)
    return _job_to_detail(job)


@router.get("/upgrade/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(UpgradeJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_to_detail(job)


@router.post("/upgrade/jobs/{job_id}/start", response_model=JobDetail)
def start_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Transition PENDING → RUNNING and enqueue per-pair orchestration.

    Idempotent on a RUNNING job (no-op). Refuses to start a job that's
    already in a terminal state.

    Dispatches one `drive_pair_task` per unique ha_pair_key. HA peers
    share a key, so a job with one standalone + one HA pair fans out
    to exactly 2 dispatches, not 3. Each dispatch runs the orchestrator
    until the next park point or done; subsequent dispatches
    (confirm/override/retry) resume from the parked phase.
    """
    job = db.get(UpgradeJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.state == JobState.RUNNING:
        return _job_to_detail(job)
    if job.state not in (JobState.PENDING,):
        raise HTTPException(
            status_code=409,
            detail=f"cannot start job in state {job.state.value}",
        )

    job.state = JobState.RUNNING
    job.started_at = datetime.now(timezone.utc)
    # Per-task audit line so the JobDetail activity panel surfaces
    # who kicked the job off (operator vs scheduler vs API client).
    for t in job.tasks:
        _audit_log(t, f"Job started by {user.username}")
    db.commit()
    db.refresh(job)

    # Commit the state transition BEFORE dispatching — if the broker is
    # slow or the dispatch raises, we don't want the job stuck in a
    # half-RUNNING limbo. The orchestrator on the worker side reads
    # job.state on entry, so it sees RUNNING when it picks up the task.
    pair_keys = sorted({t.ha_pair_key for t in job.tasks})
    for key in pair_keys:
        drive_pair_task.delay(job.id, key)

    return _job_to_detail(job)


@router.post("/upgrade/jobs/{job_id}/abort", response_model=JobDetail)
def abort_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Operator pull-the-cord on a running job.

    Sets state=ABORTED + finished_at. The orchestrator checks job.state
    on every tick and exits gracefully when it sees ABORTED, so tasks
    in flight will stop advancing on their next iteration without
    needing inter-process signal plumbing.

    Refuses on already-terminal jobs (COMPLETED / FAILED / ABORTED) —
    nothing to abort.
    """
    job = db.get(UpgradeJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.state in (JobState.COMPLETED, JobState.FAILED, JobState.ABORTED):
        raise HTTPException(
            status_code=409,
            detail=f"job already terminal ({job.state.value})",
        )
    job.state = JobState.ABORTED
    job.finished_at = datetime.now(timezone.utc)
    for t in job.tasks:
        _audit_log(t, f"Job aborted by {user.username}")
    db.commit()
    db.refresh(job)
    return _job_to_detail(job)


@router.delete(
    "/upgrade/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_job(job_id: int, db: Session = Depends(get_db)):
    """Delete a finished or aborted job and cascade its tasks.

    Refuses RUNNING (you'd lose mid-flight state — abort first).
    PENDING is deletable (no orchestrator state to lose yet).
    Cascade is configured on UpgradeJob.tasks
    (cascade="all, delete-orphan").
    """
    job = db.get(UpgradeJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.state == JobState.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="cannot delete a RUNNING job — abort first",
        )
    db.delete(job)
    db.commit()


# ---------- /upgrade/tasks/{id} ----------


# These are tasks the *operator* clicks on — DeviceUpgradeTask rows,
# not Celery tasks. The dual usage of "task" in this domain is unavoidable;
# the URL prefix makes the intent unambiguous.

_AWAITING_CONFIRM = {
    TaskPhase.AWAITING_REBOOT_CONFIRM,
    TaskPhase.AWAITING_FAILOVER_CONFIRM,
    TaskPhase.AWAITING_PRIMARY_UPGRADE_CONFIRM,
}

_AWAITING_OVERRIDE = {
    TaskPhase.AWAITING_PRECHECK_OVERRIDE,
    TaskPhase.AWAITING_POSTCHECK_OVERRIDE,
}


def _audit_log(task: DeviceUpgradeTask, message: str) -> None:
    """Append a user-attributed line to task.progress.log so the audit
    trail in the JobDetail activity panel surfaces who clicked what.

    The orchestrator's `_record` also writes here; this helper mirrors
    its shape (ISO-timestamped) so the rendered log reads as one
    chronologically-ordered stream of orchestrator + operator events.

    Uses `flag_modified` because the `progress` column is plain JSON
    (not `MutableDict.as_mutable(JSON)`), so in-place mutation followed
    by self-assignment doesn't always reliably mark the column dirty.
    `flag_modified` is the explicit "yes, persist this" signal.
    """
    progress = task.progress or {}
    log = progress.get("log") or []
    if not isinstance(log, list):
        log = []
    log.append(f"{datetime.now(timezone.utc).isoformat()} {message}")
    progress["log"] = log
    task.progress = progress
    flag_modified(task, "progress")


def _refuse_if_job_terminal(task: DeviceUpgradeTask, db: Session) -> None:
    """409 the operator's action if the parent job is terminal.

    Discovered the hard way: if the orchestrator dies (e.g. worker pod
    restart) while a task was parked at AWAITING_*_OVERRIDE, the task
    stays parked in the UI forever (its phase doesn't change). If
    something else fails the job during cleanup (timeout, abort, etc.),
    the job row goes FAILED but the task row still says "parked".

    The UI renders action buttons based on task.phase alone, so the
    operator sees Re-run / Override / Confirm buttons. They click,
    the route happily sets the confirmation_token and dispatches
    drive_pair — but `drive_pair` returns instantly because
    `_job_terminal` is True, so the click silently no-ops. Operator
    is stuck pressing buttons that do nothing.

    Fix: refuse the action loudly. The frontend also hides these
    buttons when job state is terminal (defense in depth), but the
    route is the source of truth.
    """
    job = db.get(UpgradeJob, task.job_id)
    if job is not None and job.state in (
        JobState.COMPLETED,
        JobState.FAILED,
        JobState.ABORTED,
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"job is in state {job.state.value}; action ignored. "
                "Delete this job and start a new one to recover."
            ),
        )


def _signal_task_advance(
    task: DeviceUpgradeTask, *, valid_phases: set
) -> None:
    """Tell the orchestrator's poll loop the operator wants to advance.

    The orchestrator's `_wait_for_confirm` / `_wait_for_override` loops
    poll `task.confirmation_token` waiting for it to BECOME truthy.
    When set, they clear it and return True to proceed. So the
    operator-facing endpoints just need to SET the token to any
    truthy sentinel; the orchestrator's next tick picks it up.

    Earlier code in this module tried to validate a server-issued
    token here (i.e. "expect token == previously-stored value"), but
    the orchestrator never issued one — _wait_for_override sets the
    phase and immediately polls. That mismatch meant every override
    POST 409'd with "no pending confirmation token," AND the
    frontend's button was disabled on the same empty field. Two-way
    deadlock. Fixed by making the route issue the signal rather than
    validate it; route-level auth via `current_user` is the real
    access-control gate (token-validation here was defense-in-depth
    that never actually worked given the empty-token state).

    Raises 409 if the task isn't parked at a phase that accepts this
    operation.
    """
    if task.phase not in valid_phases:
        raise HTTPException(
            status_code=409,
            detail=(
                f"task is in phase {task.phase.value}; "
                f"operation requires one of "
                f"{sorted(p.value for p in valid_phases)}"
            ),
        )
    # Any non-empty value satisfies the orchestrator's `if
    # task.confirmation_token:` check. Random + short so logs/audits
    # carry an identifier per advance, useful when correlating with
    # the orchestrator's "Operator overrode check failure at …" log
    # line.
    task.confirmation_token = secrets.token_hex(8)


@router.get("/upgrade/tasks/{task_id}", response_model=TaskDetail)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(DeviceUpgradeTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    base = _task_to_read(task)
    return TaskDetail.model_validate(
        {**base.model_dump(), "progress": task.progress}
    )


@router.post(
    "/upgrade/tasks/{task_id}/confirm", response_model=TaskDetail
)
def confirm_task(
    task_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Advance a parked task past a reboot / failover / primary-upgrade
    confirmation gate.

    The orchestrator parks at AWAITING_*_CONFIRM phases when the job
    has the corresponding `require_*` flag set. Operator clicks
    "Confirm" in the UI; we set `task.confirmation_token` to a
    sentinel so the orchestrator's polling `_wait_for_confirm` loop
    sees it on the next tick, clears it, and proceeds. Route-level
    auth gates access; no body required.
    """
    task = db.get(DeviceUpgradeTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    _refuse_if_job_terminal(task, db)
    _signal_task_advance(task, valid_phases=_AWAITING_CONFIRM)
    # Phase-aware audit line — "Confirm" means different things at
    # different gates, so capture which gate the operator cleared.
    _audit_log(
        task,
        f"Confirmed ({task.phase.value}) by {user.username}",
    )
    db.commit()
    db.refresh(task)

    # Wake the orchestrator for THIS task's pair. The previous drive_pair
    # call returned when it parked; the new one will see the set
    # confirmation_token + the current phase and advance.
    drive_pair_task.delay(task.job_id, task.ha_pair_key)

    base = _task_to_read(task)
    return TaskDetail.model_validate(
        {**base.model_dump(), "progress": task.progress}
    )


@router.post(
    "/upgrade/tasks/{task_id}/override", response_model=TaskDetail
)
def override_task(
    task_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Acknowledge a precheck or postcheck FAIL and proceed.

    Same signal-pattern as confirm_task; different valid phases. The
    task's `progress` JSON keeps the failing-check details so the
    audit trail is intact even after the operator overrides.

    Records the override under `progress.overrides` so the JobDetail
    precheck panel can render "Overridden by USERNAME" even after the
    task has moved past the gate. Useful to answer questions like
    "the device is now in Snapshot but the precheck panel still shows
    FAIL — is that because someone overrode it earlier?" without
    making the operator scroll the activity log.
    """
    task = db.get(DeviceUpgradeTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    _refuse_if_job_terminal(task, db)
    _signal_task_advance(task, valid_phases=_AWAITING_OVERRIDE)
    # Persistent override record on the task so the precheck panel
    # can surface "overridden by ..." on subsequent renders.
    phase_value = task.phase.value
    overridden_at = datetime.now(timezone.utc).isoformat()
    progress = task.progress or {}
    overrides = progress.get("overrides") or []
    if not isinstance(overrides, list):
        overrides = []
    overrides.append(
        {
            "phase": phase_value,
            "by": user.username,
            "at": overridden_at,
        }
    )
    progress["overrides"] = overrides
    task.progress = progress
    flag_modified(task, "progress")
    _audit_log(
        task,
        f"Override ({phase_value}) by {user.username}",
    )
    db.commit()
    db.refresh(task)

    drive_pair_task.delay(task.job_id, task.ha_pair_key)

    base = _task_to_read(task)
    return TaskDetail.model_validate(
        {**base.model_dump(), "progress": task.progress}
    )


@router.post(
    "/upgrade/tasks/{task_id}/rerun-check", response_model=TaskDetail
)
def rerun_task_check(
    task_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Re-run the pre- or post-check at an override gate.

    Use case: precheck flagged a real issue (e.g. candidate config
    pending). Operator fixed it externally (e.g. pushed from
    Panorama), now wants to verify the fix rather than override
    blindly.

    Implementation: set `task.confirmation_token` to a sentinel that
    starts with RERUN_ — `_wait_for_override` in the orchestrator
    recognizes it and returns its RERUN outcome to the phase
    function, which loops back and re-executes the check.
    """
    task = db.get(DeviceUpgradeTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    _refuse_if_job_terminal(task, db)
    if task.phase not in _AWAITING_OVERRIDE:
        raise HTTPException(
            status_code=409,
            detail=(
                f"task is in phase {task.phase.value}; "
                f"re-run requires one of "
                f"{sorted(p.value for p in _AWAITING_OVERRIDE)}"
            ),
        )
    # RERUN_ prefix is the protocol with _wait_for_override —
    # any token starting with this string triggers a rerun; everything
    # else falls through to PROCEED.
    task.confirmation_token = f"RERUN_{secrets.token_hex(8)}"
    _audit_log(
        task,
        f"Re-run check ({task.phase.value}) by {user.username}",
    )
    db.commit()
    db.refresh(task)

    drive_pair_task.delay(task.job_id, task.ha_pair_key)

    base = _task_to_read(task)
    return TaskDetail.model_validate(
        {**base.model_dump(), "progress": task.progress}
    )


@router.post("/upgrade/tasks/{task_id}/retry", response_model=TaskDetail)
def retry_task(
    task_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Re-enter the orchestrator for a failed or stuck task.

    Per MIGRATION_NOTES §3.3, retry == resume-from-last-completed-marker.
    `reconcile_markers_with_device_state` runs at orchestrator entry and
    fixes up the markers vs reality (e.g. clears a stale install marker
    if the device is still on the old version). Here we only need to
    flip phase off TaskPhase.FAILED back to PENDING so the next tick
    picks it up — the orchestrator does the rest.

    Refuses on tasks in DONE state (nothing to retry) or in AWAITING_*
    states (use confirm/override, not retry).
    """
    task = db.get(DeviceUpgradeTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    _refuse_if_job_terminal(task, db)
    if task.phase == TaskPhase.DONE:
        raise HTTPException(status_code=409, detail="task already DONE")
    if task.phase in _AWAITING_CONFIRM | _AWAITING_OVERRIDE:
        raise HTTPException(
            status_code=409,
            detail=(
                f"task is parked at {task.phase.value} — use "
                "/confirm or /override, not /retry"
            ),
        )
    failed_from = task.phase.value
    task.phase = TaskPhase.PENDING
    task.error = None
    _audit_log(
        task,
        f"Retry from {failed_from} by {user.username}",
    )
    # progress.completed_phases is preserved deliberately — that's what
    # makes retry resume from where it left off instead of starting over.
    db.commit()
    db.refresh(task)

    drive_pair_task.delay(task.job_id, task.ha_pair_key)

    base = _task_to_read(task)
    return TaskDetail.model_validate(
        {**base.model_dump(), "progress": task.progress}
    )
