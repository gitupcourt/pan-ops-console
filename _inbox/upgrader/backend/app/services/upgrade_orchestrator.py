"""Upgrade orchestrator state machine.

A DeviceUpgradeTask walks a sequence of TaskPhases. The orchestrator is invoked
by Celery — for each tick, it loads the current task, runs the work for the
current phase, and advances to the next phase (or parks at an awaiting_*_confirm
phase until a user calls the confirm endpoint).

Crucially, HA pairs share a `ha_pair_key`. The orchestrator uses this to:
  - Identify the secondary (passive) device first and upgrade it.
  - After post-checks, optionally park for human confirmation.
  - Trigger failover, again optionally park.
  - Upgrade the now-passive (former primary) device.
  - Optionally fail back.

Standalone devices skip the failover dance and just run precheck → snapshot →
download → install → postcheck → done.

This module currently contains only the phase-transition table and stubs. The
actual phase implementations live in `_run_phase` and call into pan_client /
panorama_client.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.enums import HARole, JobState, TaskPhase
from app.models.job import DeviceUpgradeTask, UpgradeJob


# Phase order for HA pairs (per device, with the right device picked at runtime).
HA_PHASE_ORDER = [
    TaskPhase.PRECHECK,
    TaskPhase.SNAPSHOT,
    TaskPhase.DOWNLOADING_IMAGE,
    TaskPhase.SUSPEND_SECONDARY,
    TaskPhase.UPGRADE_SECONDARY,
    TaskPhase.POSTCHECK_SECONDARY,
    TaskPhase.AWAITING_FAILOVER_CONFIRM,
    TaskPhase.FAILOVER,
    TaskPhase.AWAITING_PRIMARY_UPGRADE_CONFIRM,
    TaskPhase.UPGRADE_PRIMARY,
    TaskPhase.POSTCHECK_PRIMARY,
    TaskPhase.FAILBACK,
    TaskPhase.REPORT,
    TaskPhase.DONE,
]

STANDALONE_PHASE_ORDER = [
    TaskPhase.PRECHECK,
    TaskPhase.SNAPSHOT,
    TaskPhase.DOWNLOADING_IMAGE,
    TaskPhase.UPGRADE_PRIMARY,
    TaskPhase.POSTCHECK_PRIMARY,
    TaskPhase.REPORT,
    TaskPhase.DONE,
]


def next_phase(task: DeviceUpgradeTask, job: UpgradeJob, ha_role: HARole) -> TaskPhase:
    """Return the next TaskPhase for `task`, honoring confirmation gates and stage filters."""
    order = STANDALONE_PHASE_ORDER if ha_role == HARole.STANDALONE else HA_PHASE_ORDER

    try:
        idx = order.index(task.phase)
    except ValueError:
        # PENDING -> first phase
        return order[0]

    candidate_idx = idx + 1
    while candidate_idx < len(order):
        candidate = order[candidate_idx]

        # Skip awaiting_*_confirm phases when the job has them disabled
        if candidate == TaskPhase.AWAITING_FAILOVER_CONFIRM and not job.require_failover_confirmation:
            candidate_idx += 1
            continue
        if (
            candidate == TaskPhase.AWAITING_PRIMARY_UPGRADE_CONFIRM
            and not job.require_primary_upgrade_confirmation
        ):
            candidate_idx += 1
            continue

        # Skip failback when not requested
        if candidate == TaskPhase.FAILBACK and not job.auto_failback:
            candidate_idx += 1
            continue

        # Honor partial-workflow stage list (precheck/snapshot/postcheck etc.)
        if job.workflow_stages:
            if candidate.value not in job.workflow_stages and candidate not in (
                TaskPhase.DONE,
                TaskPhase.REPORT,
            ):
                candidate_idx += 1
                continue

        return candidate

    return TaskPhase.DONE


def tick(db: Session, task_id: int) -> None:
    """Advance one phase for `task_id`. Called from Celery."""
    task = db.get(DeviceUpgradeTask, task_id)
    if task is None or task.phase in (TaskPhase.DONE, TaskPhase.FAILED):
        return

    # TODO: implement _run_phase(task, db) -> dict|None  (sets task.progress)
    # TODO: handle exceptions -> task.phase = FAILED, task.error = str(e)
    # TODO: roll up job.state from the set of task phases

    task.tick_count += 1
    db.commit()


def task_unblocked_for_confirm(task: DeviceUpgradeTask) -> bool:
    """True if a task in an awaiting_*_confirm phase has had a confirmation submitted."""
    return task.phase in (
        TaskPhase.AWAITING_FAILOVER_CONFIRM,
        TaskPhase.AWAITING_PRIMARY_UPGRADE_CONFIRM,
    ) and task.confirmation_token is not None


def is_terminal(state: JobState) -> bool:
    return state in (JobState.COMPLETED, JobState.FAILED, JobState.ABORTED)
