"""Celery tasks that drive the upgrade orchestrator.

Phase 4d. The orchestrator (`app.upgrade.services.upgrade.drive_pair`)
is a sync function that walks one HA pair (or one standalone device)
through the per-phase state machine, blocking on PAN-OS API calls,
HA-state polling, and operator confirmation gates. A SINGLE `drive_pair`
call can therefore run for hours.

The Celery task here is the worker-side wrapper that:
  1. Owns its own DB session (drive_pair calls SessionLocal internally,
     but we don't need to manage it here — keep the wrapper thin).
  2. Reports STARTED state so the UI can distinguish "queued" from
     "actively driving" (task_track_started=True is set on celery_app).
  3. Returns a small dict for the result backend — full progress is on
     the DeviceUpgradeTask rows the orchestrator writes as it goes.

## Dispatch pattern

The orchestrator is "drive until parked." Once parked
(AWAITING_*_CONFIRM, AWAITING_*_OVERRIDE), drive_pair returns. To
advance past the park, the API routes (`/upgrade/tasks/{id}/confirm`
etc.) re-dispatch this task for the same `(job_id, ha_pair_key)`. The
orchestrator's reconcile-on-entry + completed_phases markers make the
re-entry resume from the right phase — see MIGRATION_NOTES §3.3.

Standalone devices use ha_pair_key=str(device_id); paired devices share
ha_pair_key=f"pair-{min(device_id, peer_id)}". That key derivation is
in `app.upgrade.routes.jobs._ha_pair_key_for` — keep both in sync if
the algorithm ever changes.

## Why not one task per device

Two reasons HA pairs must dispatch as ONE drive_pair call:
  1. The orchestrator serializes secondary→failover→primary within the
     pair, holding both DeviceUpgradeTask rows. Splitting them would
     race the failover.
  2. The reconcile step at entry inspects both members to decide
     whether to skip work — needs them in one context.

So the route fan-out is "one dispatch per ha_pair_key," not "one
dispatch per device."
"""

from __future__ import annotations

import logging

from app.workers.celery_app import celery

log = logging.getLogger(__name__)


# A single drive_pair invocation runs until it parks at a confirm/override gate
# or completes. With auto-reboot + auto-ack (no parks) one invocation can
# legitimately run the whole pair upgrade — two reboot waits (REBOOT_TIMEOUT_S
# each), an image download, snapshots, and checks — so the ceiling is generous.
# But it MUST be finite: a device op that hangs without honoring its read
# timeout (e.g. a Panorama proxy holding the connection open) would otherwise
# wedge a worker slot forever and stall every other pair in the job, since the
# upgrade pool is small (concurrency 2). At the SOFT limit Celery raises
# SoftTimeLimitExceeded inside drive_pair; its except-handler turns that into a
# FAILED + retryable task (slot freed, Retry resumes from the last completed
# marker — no rework). The HARD limit SIGKILLs the child as a last resort if
# the graceful path itself wedges. A rare false-positive on a genuinely slow
# upgrade just needs one Retry click, so erring generous is cheap.
UPGRADE_TASK_SOFT_TIME_LIMIT_S = 150 * 60  # 2.5 h graceful ceiling
UPGRADE_TASK_HARD_TIME_LIMIT_S = 155 * 60  # +5 m hard backstop


@celery.task(
    name="upgrade.drive_pair",
    bind=True,
    soft_time_limit=UPGRADE_TASK_SOFT_TIME_LIMIT_S,
    time_limit=UPGRADE_TASK_HARD_TIME_LIMIT_S,
)
def drive_pair_task(self, job_id: int, ha_pair_key: str) -> dict:
    """Drive one HA pair (or standalone device) until the next park or done.

    Imports the orchestrator inside the function so module-load is cheap
    (the orchestrator pulls in pan-os-python + panos-upgrade-assurance
    + the full SQLAlchemy registry — heavy). Same pattern as
    `capacity.tasks.poll_all`.

    Always returns successfully even when the orchestrator parks; the
    return dict is just observability. A genuine crash inside the
    orchestrator is caught in the `drive_pair` implementation, which fails
    THIS pair's tasks (not the whole job — failure isolation) and recomputes
    the job's terminal state (COMPLETED / COMPLETED_WITH_ERRORS / FAILED).
    Letting the celery task itself fail would queue a retry, which is the wrong
    semantics for upgrade orchestration — failures here need explicit operator
    action (Retry button), not automatic.
    """
    from app.upgrade.services.upgrade import drive_pair

    log.info(
        "upgrade.drive_pair starting: job=%s ha_pair_key=%s task_id=%s",
        job_id, ha_pair_key, self.request.id,
    )
    drive_pair(job_id, ha_pair_key)
    return {
        "status": "ok",
        "task_id": self.request.id,
        "job_id": job_id,
        "ha_pair_key": ha_pair_key,
    }
