"""End-to-end upgrade orchestration: precheck → snapshot → install → reboot
→ post-check → (HA) failover → repeat for the other member → done.

A SINGLE Celery task drives the whole flow for an HA pair (or a standalone
device). It walks the per-phase functions imperatively, persisting progress
to the DeviceUpgradeTask rows so the UI can render a live timeline.

Failures stop the job. We do NOT auto-retry, and we do NOT proceed past a
broken peer in an HA pair — leaving things half-upgraded is worse than
stopping. Confirmation gates park the driver until a user calls the confirm
endpoint, which sets task.confirmation_token; the driver polls and resumes.

This module is the "real" upgrade flow. The older stub
upgrade_orchestrator.py is left in place for now to avoid breakage with any
import paths but is not exercised.
"""

from __future__ import annotations

import enum
import logging
import time
from datetime import datetime, timezone

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from app.core.command_proxy.pan_client import (
    PanDeviceClient,
    base_image_from_list,
    feature_train,
    version_tuple,
)
from app.core.devices.models.device import Device
from app.core.devices.models.enums import HARole
from app.db import SessionLocal
from app.upgrade.models.enums import JobState, Severity, TaskPhase
from app.upgrade.models.job import DeviceUpgradeTask, UpgradeJob
from app.upgrade.models.precheck import PrecheckRun
from app.upgrade.models.precheck_set import PrecheckSet
from app.upgrade.models.snapshot import Snapshot, SnapshotKind
from app.upgrade.services import precheck as precheck_svc
from app.upgrade.tasks import drive_pair_task
from app.core.concurrency import dispatch_lock
from app.upgrade.services import snapshot as snapshot_svc
from app.upgrade.services.precheck_classifier import classify, overall_severity

log = logging.getLogger(__name__)

# How long to wait for a device to come back from a reboot.
REBOOT_TIMEOUT_S = 30 * 60
REBOOT_POLL_S = 30
# Consecutive healthy post-reboot checks (reachable AND on the target version)
# required before declaring the device "up". Re-implements wait_for_ready's
# consecutive-success confirmation (its default was 3) for the non-blocking,
# park-between-checks reboot wait: a single flaky mgmt response mid-reboot (the
# API answers briefly, then the box goes back down as subsystems init) must not
# read as "up". The streak is persisted on task.progress across re-dispatches.
REBOOT_READY_CONSECUTIVE = 3

# How long to wait for an install JOB to FIN before assuming it's wedged.
INSTALL_JOB_TIMEOUT_S = 30 * 60
INSTALL_JOB_POLL_S = 20

# A software download can transiently fail when the device is already busy with
# another download (a content/dynamic-update pull, or a leftover download from a
# prior job): PAN-OS returns "Another download is in progress. Please try again
# later". That's not a real failure — wait and retry a few times.
DOWNLOAD_RETRY_ATTEMPTS = 3
DOWNLOAD_RETRY_WAIT_S = 60

# A software install can transiently fail when the device is already mid-install
# (a leftover install from a prior attempt, or — the real culprit — a duplicate
# drive_pair dispatch racing the first): PAN-OS returns "A software install is in
# progress. Please try again later." Wait + retry, same as downloads.
INSTALL_RETRY_ATTEMPTS = 3
INSTALL_RETRY_WAIT_S = 60

# Per-(job, pair) dispatch-lock TTL. The lock makes a duplicate drive_pair
# dispatch a no-op while one is actively driving the pair. Held only for a
# single drive segment (drive_pair returns — releasing it — at each gate park),
# so 30 min comfortably covers an install/reboot/HA-resume segment between gates
# while bounding how long a hard-killed holder blocks redelivery recovery.
_PAIR_LOCK_TTL_S = 30 * 60

# Re-dispatch delay for the NON-BLOCKING long waits (install-job FIN, reboot
# readiness, image-download FIN, HA-resync to 'passive'). Like the confirm
# gates, these waits no longer block the worker slot by polling in a loop:
# _await_or_park checks the condition ONCE and, if not yet satisfied, parks the
# task and re-dispatches drive_pair after this many seconds. Each re-dispatch
# re-enters, re-checks, and either advances or re-parks. 30s keeps the timeline
# responsive without hammering the device/Panorama proxy, and frees the slot for
# other pairs the entire time (the whole point of issue #185).
NONBLOCKING_POLL_S = 30

# pre_stage_mode values that mean "stage, don't upgrade now". Prechecks run for
# information in these modes but must NOT gate (you're only downloading an
# image; nothing risky happens until the operator proceeds to install).
_STAGING_MODES = frozenset({"stage_only", "hold"})

# Confirmation gates (reboot / failover / primary-upgrade / install-hold) are
# NON-BLOCKING: they park the task and return, freeing the worker slot, then
# resume on the /confirm route's re-dispatch. There is therefore no in-worker
# poll loop or timeout to tune — a pause can last as long as the operator needs.

# How long after a failover to wait for HA states to settle.
FAILOVER_SETTLE_S = 60

# How long to poll for the upgraded device to re-enter HA as 'passive'.
HA_PASSIVE_TIMEOUT_S = 10 * 60
HA_PASSIVE_POLL_S = 15

# After mgmt plane comes back post-reboot, how long to wait for the HA
# subsystem (separate daemon) to be ready to accept control commands.
# The mgmt API can return get_system_info long before the HA daemon
# settles — issuing resume/suspend during that window fails. Generous
# to accommodate slower firewalls and post-reboot subsystem-by-subsystem
# initialization (PA-5xx, big VM-Series can take 20+ minutes overall).
HA_SUBSYS_TIMEOUT_S = 30 * 60
HA_SUBSYS_POLL_S = 15

# How many times to retry HA control ops (suspend / resume / failover) when
# the device replies with connection-refused or similar transient errors.
HA_OP_RETRIES = 5
HA_OP_RETRY_SLEEP_S = 30

# How many times to re-issue resume_ha when the device sits in 'suspended'
# after a successful-looking API call, and how long to wait for the state
# to actually move between each re-issue. 3 × 30s = 90s of patience before
# we give up — keeps the failure mode bounded so an obvious "PAN ignored
# the API" doesn't eat the full 10-min wait_for_passive timeout.
RESUME_VERIFY_REISSUES = 3
RESUME_VERIFY_WAIT_S = 30

# After reboot + HA-resume, the device's own mgmt plane is back (wait_for_ready
# cleared) but the managing Panorama can take extra time to re-establish its
# connection to the device. The postcheck runs through the upgrade route
# (Panorama-proxied by default), so firing it during that reconnect window
# yields a "Panorama unreachable" runner error and a postcheck that validated
# nothing. Poll the route until a light op answers before running the check.
# 5 min covers the typical re-registration lag without stalling the job.
POSTCHECK_ROUTE_TIMEOUT_S = 5 * 60
POSTCHECK_ROUTE_POLL_S = 20


def _resolve_checks_for_job(db: Session, job: UpgradeJob) -> list[str] | None:
    """Return the readiness-check name list to run for this job.

    Resolution order:
      1. job.precheck_set_id explicitly set → use that set's checks.
      2. Else: the PrecheckSet flagged `is_default=True` (seeded as
         "Standard" by migration 0007).
      3. Else: return None — the precheck service then falls back to
         the hard-coded DEFAULT_READINESS_CHECKS.

    Returning None instead of inlining DEFAULT_READINESS_CHECKS here
    keeps the per-call-site code identical to the pre-Phase-13b
    behaviour (run_precheck_for_device's own default kicks in) and
    avoids importing the constant into this module.
    """
    if job.precheck_set is not None:
        return list(job.precheck_set.checks)
    # Fall back to whichever set the operator flagged default.
    default_set = (
        db.execute(
            sa_select(PrecheckSet).where(PrecheckSet.is_default.is_(True))
        )
        .scalars()
        .first()
    )
    if default_set is not None:
        return list(default_set.checks)
    return None  # let the precheck service apply its own default


# ---------- public entry points ----------


def drive_pair(job_id: int, ha_pair_key: str) -> None:
    """Public entry — drive one HA pair / standalone, guarded by a per-pair
    dispatch lock.

    A duplicate dispatch for a ``(job, ha_pair_key)`` that's already being
    driven is a NO-OP, not a second concurrent drive that races the first. That
    race is what aborted pairs in job 10: an operator clicked "Proceed to
    install" on both HA members (and again when nothing visibly happened), each
    click re-dispatched the pair, and the duplicate re-classified a mid-upgrade
    pair → "HA roles unclear" / install collision. The lock is non-blocking
    (the duplicate returns immediately) and TTL-expiring (a crashed holder can't
    wedge the pair; acks_late handles worker-loss redelivery once the TTL
    clears).
    """
    with dispatch_lock(
        f"upgrade:drive_pair:{job_id}:{ha_pair_key}", ttl=_PAIR_LOCK_TTL_S
    ) as acquired:
        if not acquired:
            log.info(
                "drive_pair(%s, %s): already being driven; skipping duplicate dispatch",
                job_id, ha_pair_key,
            )
            return
        _drive_pair_inner(job_id, ha_pair_key)


def _drive_pair_inner(job_id: int, ha_pair_key: str) -> None:
    """Run the upgrade for one HA pair (2 tasks) or one standalone (1 task).

    Owns its own DB session — Celery tasks should not share the FastAPI
    request-scoped session.
    """
    db = SessionLocal()
    try:
        job = db.get(UpgradeJob, job_id)
        if job is None:
            log.warning("drive_pair: job %s not found", job_id)
            return
        tasks = (
            db.query(DeviceUpgradeTask)
            .filter(
                DeviceUpgradeTask.job_id == job_id,
                DeviceUpgradeTask.ha_pair_key == ha_pair_key,
            )
            .all()
        )
        if not tasks:
            log.warning("drive_pair: no tasks for job=%s key=%s", job_id, ha_pair_key)
            return

        if _job_terminal(db, job):
            # If the job ended (especially ABORTED) with a member we left
            # suspended, restore HA before bailing — never leave a cluster
            # degraded on a single node. (The abort route re-dispatches each
            # pair so this cleanup runs.)
            _heal_suspended_members(db, job, tasks)
            return

        # Reconcile each task's phase markers against the device's CURRENT
        # state. This is what makes Retry actually do the right thing when
        # the device's reality has diverged from what the markers claim —
        # e.g. resume_ha returned 200 but the firewall silently ignored it
        # and the device is still suspended. Without this we'd skip the
        # resume on retry and never recover. See
        # reconcile_markers_with_device_state for the rules.
        for t in tasks:
            try:
                reconcile_markers_with_device_state(db, job, t, t.device)
            except Exception:  # noqa: BLE001
                log.exception("Reconciliation failed for task %s; continuing", t.id)

        if len(tasks) == 1:
            _drive_solo(db, job, tasks[0])
        else:
            _drive_ha_pair(db, job, tasks)

        # If any member of this pair failed, fail the whole pair atomically
        # (you can't half-upgrade an HA pair) so the partner doesn't sit
        # non-terminal forever and the job can reach a terminal state.
        _fail_pair_if_partial(db, tasks)
        # Restore HA on any member we suspended if the pair failed OR the job was
        # aborted out from under this drive — a failed/aborted upgrade must not
        # leave the cluster degraded on a single node.
        db.refresh(job)
        if job.state == JobState.ABORTED or any(t.phase == TaskPhase.FAILED for t in tasks):
            _heal_suspended_members(db, job, tasks)
        # Recompute the job state. Only flips terminal once EVERY task across
        # the job is terminal (COMPLETED / COMPLETED_WITH_ERRORS / FAILED) —
        # other pairs in flight or parked keep it RUNNING.
        _maybe_mark_job_done(db, job_id)
    except Exception as exc:  # noqa: BLE001 — also catches SoftTimeLimitExceeded
        log.exception("drive_pair crashed for job=%s key=%s", job_id, ha_pair_key)
        # A SoftTimeLimitExceeded means the task blew past its time ceiling —
        # almost always a device/Panorama op hanging without honoring its read
        # timeout. A hung task must never wedge the small upgrade worker pool
        # (it blocks every other pair in the job), so the ceiling converts it
        # into a clean, retryable failure that frees the slot.
        msg = _driver_failure_message(exc)
        # Make the failure RECOVERABLE: flip any still-in-flight task to FAILED
        # (leaving DONE tasks alone) so it lands in a retryable state, the UI
        # offers Retry, and resume picks up from the last completed marker. A
        # bare driver crash used to freeze tasks mid-phase with no way forward.
        # Best-effort — cleanup must never mask the original error.
        try:
            crash_tasks = (
                db.query(DeviceUpgradeTask)
                .filter(
                    DeviceUpgradeTask.job_id == job_id,
                    DeviceUpgradeTask.ha_pair_key == ha_pair_key,
                )
                .all()
            )
            for t in crash_tasks:
                if t.phase not in (TaskPhase.DONE, TaskPhase.FAILED):
                    _record(db, t, msg, phase=TaskPhase.FAILED)
            # A crash mid-upgrade must not leave the cluster degraded — restore
            # HA on any member we'd suspended.
            crash_job = db.get(UpgradeJob, job_id)
            _heal_suspended_members(db, crash_job, crash_tasks)
        except Exception:  # noqa: BLE001
            log.exception("post-crash task cleanup failed for job=%s", job_id)
        # The loop above already failed this pair's in-flight tasks (atomic pair
        # failure). Record the reason, then recompute the job state — OTHER
        # pairs are unaffected and the job only goes terminal once they all are.
        _fail_job(db, job_id, msg[:500])
        _maybe_mark_job_done(db, job_id)
    finally:
        db.close()


# ---------- pre-stage gate ----------


def _pre_stage_stop(db: Session, job: UpgradeJob, tasks: list[DeviceUpgradeTask]) -> bool:
    """Stage-only gate, applied AFTER precheck + image-download + pre-snapshot
    and BEFORE any install/reboot.

    For a ``pre_stage_mode == "stage_only"`` job, mark the given task(s) staged
    + DONE and return True so the caller STOPS before install. The slow work
    (precheck, image download, pre-snapshot) has already run, so a later real
    upgrade job installs fast. Returns False for a normal (full) job — proceed.
    """
    if (job.pre_stage_mode or "none") != "stage_only":
        return False
    for t in tasks:
        _record(
            db, t,
            "Stage-only job: precheck, image download, and pre-snapshot complete "
            "— install and reboot skipped. The image is downloaded; run a normal "
            "upgrade job to install when ready.",
        )
        _set_phase(db, t, TaskPhase.DONE)
    return True


def _pre_stage_hold_gate(
    db: Session,
    job: UpgradeJob,
    gate_task: DeviceUpgradeTask,
    *,
    pair_tasks: list[DeviceUpgradeTask] | None = None,
) -> bool:
    """Stage-and-hold gate (``pre_stage_mode == "hold"``), after precheck +
    image + pre-snapshot and before install.

    NON-BLOCKING (like ``_confirm_gate``): parks the task(s) at
    AWAITING_INSTALL_CONFIRM and RETURNS False so drive_pair exits and frees the
    worker slot until the operator clicks "Proceed to install". The /confirm
    route sets confirmation_token and re-dispatches; on re-entry the token is
    consumed, ``install_confirmed`` is marked on ``gate_task`` (so a later
    re-dispatch never re-parks), and we proceed.

    For an HA pair, pass BOTH members via ``pair_tasks``: otherwise the non-gate
    member sits at DOWNLOADING_IMAGE forever (it finished downloading but nothing
    moved its phase), which reads as "stuck". With both passed, both members show
    the held state and Proceed works from either member's button.

    Returns True to proceed to install, False to STOP this invocation (parked —
    NOT a failure).
    """
    if (job.pre_stage_mode or "none") != "hold":
        return True
    tasks = pair_tasks or [gate_task]
    if _phase_already_done(gate_task, "install_confirmed"):
        return True
    for t in tasks:
        db.refresh(t)
    # Proceed if the operator confirmed on ANY member of the pair.
    if any(t.confirmation_token for t in tasks):
        for t in tasks:
            t.confirmation_token = None
        db.commit()
        _mark_phase_done(db, gate_task, "install_confirmed")
        _record(db, gate_task, "Proceed-to-install confirmed; installing now.")
        return True
    for t in tasks:
        _record(
            db, t,
            "Staged — precheck, image download, and pre-snapshot complete. Paused "
            "before install; click 'Proceed to install' when ready.",
            phase=TaskPhase.AWAITING_INSTALL_CONFIRM,
        )
    return False


def _confirm_gate(
    db: Session,
    task: DeviceUpgradeTask,
    parking_phase: TaskPhase,
    marker: str,
    *,
    parked_msg: str,
    proceed_msg: str,
) -> bool:
    """Non-blocking operator-confirmation gate — the shared primitive behind the
    reboot / failover / primary-upgrade / install-hold pauses.

    Replaces the old blocking ``_wait_for_confirm`` poll loop. Instead of
    sleeping in the worker (which held a concurrency slot for the *entire* wait
    and was capped by the task time limit — a single parked pair could starve
    every other pair in the job), this parks ``task`` at ``parking_phase`` and
    RETURNS False so ``drive_pair`` exits and frees the slot. The /confirm route
    sets ``confirmation_token`` and re-dispatches ``drive_pair``; on re-entry the
    token is consumed, ``marker`` is recorded (so a later re-dispatch never
    re-parks here), and we return True to proceed. A pause can now last
    hours/days without tying up a worker.

    Returns True to proceed past the gate, False to STOP this invocation
    (parked — drive_pair returns cleanly; this is NOT a failure).
    """
    if _phase_already_done(task, marker):
        return True
    db.refresh(task)
    if task.confirmation_token:
        task.confirmation_token = None
        db.commit()
        _mark_phase_done(db, task, marker)
        _record(db, task, proceed_msg)
        return True
    _record(db, task, parked_msg, phase=parking_phase)
    return False


class _Wait(enum.Enum):
    """Outcome of a single NON-BLOCKING long-wait check (`_await_or_park`).

    The four long waits (install-job FIN, post-reboot readiness, image-download
    FIN, HA-resync to 'passive') used to BLOCK the worker by polling in a
    `while` loop — with worker --concurrency 2, two such waits stalled the whole
    job (issue #185). They now mirror the confirm-gate park pattern: check the
    condition ONCE per drive_pair entry and, if not yet satisfied, park + free
    the slot, resuming on a self-scheduled re-dispatch.

      - DONE:      the awaited condition is satisfied; the caller falls through.
      - PARKED:    not satisfied yet, but still within the timeout — drive_pair
                   has been re-dispatched (countdown). The caller must
                   `return False` so the worker slot frees until then.
      - TIMED_OUT: the persisted start exceeded timeout_s — the caller records
                   FAILED + _fail_job and returns False.
    """

    DONE = "done"
    PARKED = "parked"
    TIMED_OUT = "timed_out"


def _await_or_park(
    db: Session,
    task: DeviceUpgradeTask,
    *,
    marker: str,
    timeout_s: int,
    is_done,
    poll_s: int = NONBLOCKING_POLL_S,
) -> _Wait:
    """Non-blocking replacement for a blocking poll loop — the shared primitive
    behind the install/reboot/download/HA-resync waits.

    Instead of sleeping in the worker until ``is_done()`` turns true (which held
    a concurrency slot for the entire multi-minute wait), this checks ONCE and,
    if not done, re-dispatches ``drive_pair`` after ``poll_s`` and returns PARKED
    so the caller exits and frees the slot. Each re-dispatch re-enters and
    re-checks — the wait makes progress across many short invocations rather than
    one long block, so independent pairs run in parallel and no single drive_pair
    invocation can trip the Celery soft/hard time limit.

    The timeout must survive re-entries (each invocation is a fresh process with
    no in-memory deadline), so we persist an ISO start timestamp at
    ``progress["_wait_start::<marker>"]`` on the FIRST check and compare against
    it on every subsequent one. The per-marker key lets several distinct waits
    coexist on one task's progress over the life of the upgrade. Uses the
    dict-copy + reassign + commit pattern (the ``progress`` column is plain JSON,
    NOT MutableDict — see ``_mark_phase_done`` for why).

    ``is_done`` is called exactly once and wrapped in try/except: a transient
    error (Panorama still re-registering the just-rebooted device, HA daemon not
    answering yet, a dropped socket) counts as "not done yet" and parks, rather
    than aborting the upgrade — the same resilience the old poll loops had via
    their per-iteration ``except: continue``.

    Returns DONE / PARKED / TIMED_OUT (see ``_Wait``). The start marker is
    cleared on the terminal outcomes (DONE / TIMED_OUT) so a later wait that
    reuses the same marker starts a fresh clock.
    """
    start_key = f"_wait_start::{marker}"
    progress = dict(task.progress or {})
    start_iso = progress.get(start_key)
    if start_iso is None:
        # First check for this wait — stamp the clock so the timeout survives
        # the re-dispatches that follow.
        start_iso = _now_iso()
        progress[start_key] = start_iso
        task.progress = progress
        db.commit()

    # Check the condition exactly once. A transient failure is "not done yet".
    try:
        done = bool(is_done())
    except Exception as exc:  # noqa: BLE001
        log.info(
            "_await_or_park(%s): is_done() raised (treating as not-done): %s",
            marker, exc,
        )
        done = False

    if done:
        _clear_wait_start(db, task, start_key)
        return _Wait.DONE

    # Not done — has the persisted clock run out?
    try:
        start_dt = datetime.fromisoformat(start_iso)
    except (TypeError, ValueError):
        # Corrupt/missing timestamp — re-stamp NOW and treat this as the first
        # tick rather than instantly timing out on garbage.
        start_dt = datetime.now(timezone.utc)
        progress = dict(task.progress or {})
        progress[start_key] = start_dt.isoformat()
        task.progress = progress
        db.commit()
    if (datetime.now(timezone.utc) - start_dt).total_seconds() >= timeout_s:
        _clear_wait_start(db, task, start_key)
        return _Wait.TIMED_OUT

    # Still within the window — re-dispatch ourselves and park. apply_async with
    # the (job_id, ha_pair_key) positional args keeps the same `upgrade` queue
    # routing (task_routes on "upgrade.*"); the per-pair dispatch_lock releases
    # when drive_pair returns (context-manager exit) and the countdown
    # re-dispatch re-acquires it — no lock changes needed.
    drive_pair_task.apply_async(
        (task.job_id, task.ha_pair_key), countdown=poll_s
    )
    return _Wait.PARKED


def _clear_wait_start(db: Session, task: DeviceUpgradeTask, start_key: str) -> None:
    """Drop a persisted ``_await_or_park`` start timestamp. Idempotent — no-op if
    the key isn't present. Dict-copy + reassign (plain-JSON column)."""
    progress = dict(task.progress or {})
    if start_key in progress:
        progress.pop(start_key, None)
        task.progress = progress
        db.commit()


def _resolve_override_action(
    db: Session, task: DeviceUpgradeTask
) -> "_OverrideOutcome | None":
    """Inspect ``confirmation_token`` at a NON-BLOCKING override gate.

    Called at the top of ``_phase_precheck`` / ``_phase_postcheck`` when the task
    is re-entered while parked at AWAITING_*_OVERRIDE (the /override or
    /rerun-check route set a token and re-dispatched drive_pair). Returns:

      - PROCEED: operator clicked "Override + proceed" — token consumed; the
        caller marks the check done and advances.
      - RERUN:   operator clicked "Re-run check" — token consumed; the caller
        re-executes the check fresh.
      - ABORT:   the job is terminal (operator aborted, or another task failed
        it) — the caller bails.
      - None:    no token yet — still parked; the caller returns so drive_pair
        exits and the slot stays free until the operator acts.
    """
    job = db.get(UpgradeJob, task.job_id)
    if job is None or job.state in _TERMINAL_JOB_STATES:
        return _OverrideOutcome.ABORT
    db.refresh(task)
    tok = task.confirmation_token
    if not tok:
        return None
    task.confirmation_token = None
    db.commit()
    if tok.startswith(_RERUN_TOKEN_PREFIX):
        return _OverrideOutcome.RERUN
    return _OverrideOutcome.PROCEED


# ---------- standalone flow ----------


def _drive_solo(db: Session, job: UpgradeJob, task: DeviceUpgradeTask) -> None:
    device = task.device

    if not _phase_precheck(db, job, task, device):
        return
    if not _phase_snapshot(db, job, task, device):
        return
    if not _phase_ensure_image(db, job, task, device):
        return
    if _pre_stage_stop(db, job, [task]):
        return
    if not _pre_stage_hold_gate(db, job, task):
        return
    if not _phase_install_and_wait(db, job, task, device):
        return
    if not _phase_postcheck(db, job, task, device):
        return
    # Post-snapshot + diff. Non-fatal — we already passed postcheck, so
    # the upgrade succeeded; the diff is for the audit trail.
    _phase_post_snapshot_and_diff(db, job, task, device)

    _set_phase(db, task, TaskPhase.DONE)


# ---------- HA pair flow ----------


def _drive_ha_pair(db: Session, job: UpgradeJob, tasks: list[DeviceUpgradeTask]) -> None:
    # Identify which task is on the passive member (we upgrade that one first).
    passive, active = _classify_pair(db, tasks)
    if passive is None or active is None:
        # Roles unclear. Common cause: a member is stuck 'suspended' from an
        # earlier interrupted upgrade (a member reads role "unknown" while
        # suspended). Try to heal it (resume → re-probe) and re-classify before
        # giving up — this self-recovers a pair an aborted job left degraded.
        if _heal_suspended_for_classify(db, job, tasks):
            passive, active = _classify_pair(db, tasks)
    if passive is None or active is None:
        for t in tasks:
            _record(db, t, "Could not determine HA roles for the pair; aborting", phase=TaskPhase.FAILED)
        _fail_job(db, job.id, "HA roles unclear")
        return

    # Pre-check + snapshot both members up front. If either fails precheck,
    # don't proceed.
    for t in (passive, active):
        if not _phase_precheck(db, job, t, t.device):
            return
    for t in (passive, active):
        if not _phase_snapshot(db, job, t, t.device):
            # Snapshot failure is non-fatal; we log and continue.
            pass

    # Early-out: both halves of the pair are already running the target
    # version AND in normal HA states. Nothing to install, no point
    # disrupting the cluster with a failover.
    #
    # The HA health check is critical: if a previous attempt installed the
    # passive but failed at HA resume, the device is at target version but
    # still in 'suspended' / 'initial'. Skipping work would leave it broken.
    # When HA is unhealthy, we proceed to the normal flow — the per-phase
    # checks will skip install but the HA resume block in
    # _phase_install_and_wait will run.
    db.refresh(passive.device)
    db.refresh(active.device)
    if (
        _is_already_at_target(passive.device, job.target_version)
        and _is_already_at_target(active.device, job.target_version)
        and _is_ha_healthy(passive.device)
        and _is_ha_healthy(active.device)
    ):
        _record(
            db, passive,
            f"Both HA members already running {job.target_version} and HA-healthy; no upgrade work needed",
        )
        _record(
            db, active,
            f"Both HA members already running {job.target_version} and HA-healthy; no upgrade work needed",
        )
        _set_phase(db, passive, TaskPhase.DONE)
        _set_phase(db, active, TaskPhase.DONE)
        return

    # Ensure the image is on both members (download if not). The per-phase
    # check skips this for any device already at target.
    for t in (passive, active):
        if not _phase_ensure_image(db, job, t, t.device):
            return

    # Stage-only job: both members are staged (precheck + image + pre-snapshot
    # done); stop before any suspend/install/failover.
    if _pre_stage_stop(db, job, [passive, active]):
        return

    # Stage-and-hold: park the WHOLE pair until the operator clicks "Proceed to
    # install". Non-blocking — frees the slot. Both members are passed so the
    # active one shows the held state too (not a stale DOWNLOADING_IMAGE) and
    # Proceed works from either.
    if not _pre_stage_hold_gate(db, job, passive, pair_tasks=[passive, active]):
        return

    # ---- Phase: upgrade the passive ----
    if not _phase_suspend_ha(db, job, passive, passive.device):
        return
    _set_phase(db, passive, TaskPhase.UPGRADE_SECONDARY)
    if not _phase_install_and_wait(db, job, passive, passive.device, started_phase=TaskPhase.UPGRADE_SECONDARY):
        return
    # _phase_postcheck sets POSTCHECK_SECONDARY itself (after its resume check),
    # so we must NOT set it here — doing so would clobber a parked
    # AWAITING_POSTCHECK_OVERRIDE phase and defeat the non-blocking resume.
    if not _phase_postcheck(db, job, passive, passive.device, finishing_phase=TaskPhase.POSTCHECK_SECONDARY):
        return
    _phase_post_snapshot_and_diff(db, job, passive, passive.device)

    # ---- Phase: failover ----
    if job.require_failover_confirmation:
        if not _confirm_gate(
            db, passive,
            TaskPhase.AWAITING_FAILOVER_CONFIRM,
            "failover_confirmed",
            parked_msg=(
                "Passive member upgraded. Awaiting confirmation to fail service "
                "over to it before upgrading the active member."
            ),
            proceed_msg="Failover confirmed; failing over now.",
        ):
            return
    _set_phase(db, passive, TaskPhase.FAILOVER)
    if not _phase_failover(db, job, active, passive):
        return

    # The failover itself is now complete — the (former) passive is the
    # active member serving traffic, and it has nothing more to do until
    # the whole pair finishes. Without this, its badge sits frozen on
    # "FAILOVER" for the entire (often 30+ min) primary-upgrade window.
    # The sub-step makes it read "FAILOVER · partner upgrading" so the
    # operator knows it's parked-by-design, not stuck. It flips to DONE
    # at the end of the pair flow.
    _set_substep(db, passive, "partner_upgrading")

    # ---- Optional gate before upgrading the (former) active ----
    if job.require_primary_upgrade_confirmation:
        if not _confirm_gate(
            db, active,
            TaskPhase.AWAITING_PRIMARY_UPGRADE_CONFIRM,
            "primary_upgrade_confirmed",
            parked_msg=(
                "Failover complete; the upgraded member is now serving traffic. "
                "Awaiting confirmation to upgrade the former active member."
            ),
            proceed_msg=(
                "Primary-upgrade confirmed; upgrading the former active member now."
            ),
        ):
            return

    # ---- Phase: upgrade the (former) active, now passive ----
    if not _phase_suspend_ha(db, job, active, active.device):
        return
    _set_phase(db, active, TaskPhase.UPGRADE_PRIMARY)
    if not _phase_install_and_wait(db, job, active, active.device, started_phase=TaskPhase.UPGRADE_PRIMARY):
        return
    # See the passive postcheck above — _phase_postcheck owns the phase set.
    if not _phase_postcheck(db, job, active, active.device, finishing_phase=TaskPhase.POSTCHECK_PRIMARY):
        return
    _phase_post_snapshot_and_diff(db, job, active, active.device)

    # Optional failback (return service to the original active).
    if job.auto_failback:
        _set_phase(db, active, TaskPhase.FAILBACK)
        if _ha_op_with_retry(
            db, active, passive.device, "Failback (suspend current active)",
            lambda c: c.trigger_failover(),
        ):
            time.sleep(FAILOVER_SETTLE_S)
        # Failback failure is non-fatal — both devices are upgraded; the
        # operator can fail back manually.

    _set_phase(db, passive, TaskPhase.DONE)
    _set_phase(db, active, TaskPhase.DONE)


def _classify_pair(
    db: Session,
    tasks: list[DeviceUpgradeTask],
) -> tuple[DeviceUpgradeTask | None, DeviceUpgradeTask | None]:
    """Determine which task is the passive (upgrade-first) member and which is
    the active. The assignment is fixed for the life of the pair upgrade, so we
    PERSIST it on the first successful classify and reuse it thereafter.

    Why persist: live `device.ha_role` is only reliable when the pair is in a
    settled state. Mid-upgrade a member is suspended/installing/rebooting and
    reports its role as "unknown" — re-reading live roles on a re-entry then
    fails to find a passive+active and aborts the whole pair ("Could not
    determine HA roles"). That's exactly what killed homegateway in job 10 when
    a duplicate dispatch re-classified it mid-install. Reusing the persisted
    assignment makes re-entry (Retry, worker-redelivery, the proceed gates)
    role-stable. Note the assignment is who-upgrades-first, which stays fixed
    even though the live roles swap at failover.
    """
    # Prefer the persisted assignment if both members carry one.
    persisted_passive = next(
        (t for t in tasks if (t.progress or {}).get("ha_member") == "passive"), None
    )
    persisted_active = next(
        (t for t in tasks if (t.progress or {}).get("ha_member") == "active"), None
    )
    if persisted_passive is not None and persisted_active is not None:
        return persisted_passive, persisted_active

    # First classify from live HA roles, and persist it so future re-entries
    # don't depend on a transient live read.
    passive = next((t for t in tasks if t.device.ha_role == HARole.PASSIVE), None)
    active = next((t for t in tasks if t.device.ha_role == HARole.ACTIVE), None)
    if passive is not None and active is not None:
        _persist_member_role(db, passive, "passive")
        _persist_member_role(db, active, "active")
    return passive, active


def _persist_member_role(db: Session, task: DeviceUpgradeTask, role: str) -> None:
    """Record this task's fixed pair role ("passive"/"active") on its progress so
    re-entry classification is stable. Dict-copy + reassign (the column is plain
    JSON — see _mark_phase_done for the rationale)."""
    progress = dict(task.progress or {})
    if progress.get("ha_member") == role:
        return
    progress["ha_member"] = role
    task.progress = progress
    db.commit()


# ---------- HA heal (don't leave a cluster degraded on failure) ----------


def _resume_suspended_member(
    db: Session, task: DeviceUpgradeTask, device: Device
) -> bool:
    """Resume one HA member that is currently 'suspended' and confirm it left
    that state. Returns True if the member is not (or no longer) suspended,
    False if a resume couldn't be confirmed. Never raises — healing is
    best-effort and must not mask the original failure."""
    try:
        db.refresh(device)
        if device.ha_peer_id is None:
            return True  # standalone — nothing to resume
        if (device.ha_state or "").lower().strip() != "suspended":
            return True  # already healthy
        if not _ha_op_with_retry(
            db, task, device, "HA resume (restore cluster)", lambda c: c.resume_ha()
        ):
            _record(
                db, task,
                "Could not resume HA (command failed); resume manually: "
                "`request high-availability state functional`.",
            )
            return False
        if not _verify_resume_took_effect(db, task, device):
            _record(
                db, task,
                "HA resume issued but the member stayed suspended; resume "
                "manually: `request high-availability state functional`.",
            )
            return False
        _record(db, task, "HA resumed — member rejoined the cluster (redundancy restored).")
        return True
    except Exception as exc:  # noqa: BLE001 — heal is best-effort
        log.warning("HA resume (restore) errored for task %s: %s", task.id, exc)
        try:
            _record(db, task, f"HA resume errored ({exc}); resume manually if the member stays suspended.")
        except Exception:  # noqa: BLE001
            pass
        return False


def _heal_suspended_members(
    db: Session, job: UpgradeJob, tasks: list[DeviceUpgradeTask]
) -> None:
    """Restore HA after a pair's upgrade ENDS without completing (phase failure,
    crash, or abort). We suspend the passive to install it; a failure between
    that suspend and the normal resume would otherwise leave the member
    'suspended' — the cluster running on a single node with no redundancy, and
    unclassifiable for the next job (the job-10 → job-11 trap). Resume any such
    member, best-effort. Members already resumed in the normal flow
    (ha_resume_complete) are skipped."""
    for t in tasks:
        if _phase_already_done(t, "ha_resume_complete"):
            continue
        _resume_suspended_member(db, t, t.device)


def _heal_suspended_for_classify(
    db: Session, job: UpgradeJob, tasks: list[DeviceUpgradeTask]
) -> bool:
    """A pair failed to classify (no clean passive+active). If that's because a
    member is stuck 'suspended' — left by an earlier interrupted upgrade —
    resume it so the pair becomes classifiable, then re-probe so the caller's
    re-classify reads settled roles. Returns True if a resume was attempted."""
    attempted = False
    for t in tasks:
        db.refresh(t.device)
        if t.device.ha_peer_id is None:
            continue
        if (t.device.ha_state or "").lower().strip() != "suspended":
            continue
        _record(
            db, t,
            "HA member is suspended — likely left by an earlier interrupted "
            "upgrade. Resuming to restore the cluster before classifying.",
        )
        if _resume_suspended_member(db, t, t.device):
            attempted = True
    if attempted:
        for t in tasks:
            try:
                precheck_svc.probe_device(db, t.device, client=_client_for(db, t.device))
                db.refresh(t.device)
            except Exception as exc:  # noqa: BLE001
                log.info("post-resume re-probe failed for %s: %s", t.device.name, exc)
    return attempted


# ---------- phase functions ----------


def _phase_precheck(db: Session, job: UpgradeJob, task: DeviceUpgradeTask, device: Device) -> bool:
    if _phase_already_done(task, "precheck"):
        _record(db, task, "Pre-check already completed in a prior run; skipping")
        return True

    # Resume from a non-blocking override park. If we previously parked at the
    # precheck override gate, the operator's choice (Override / Re-run / Abort)
    # arrives as a re-dispatch of drive_pair — handle the token here BEFORE
    # deciding whether to re-run the check. (This was a blocking poll loop
    # inside _wait_for_override that held the worker slot for the whole wait.)
    if task.phase == TaskPhase.AWAITING_PRECHECK_OVERRIDE:
        action = _resolve_override_action(db, task)
        if action is None or action == _OverrideOutcome.ABORT:
            return False  # no token yet (stay parked) / job aborted
        if action == _OverrideOutcome.PROCEED:
            _record(db, task, "Operator overrode pre-check failure; proceeding.")
            _mark_phase_done(db, task, "precheck")
            return True
        # RERUN — drop stale results and fall through to execute fresh.
        progress = dict(task.progress or {})
        progress.pop("failing_checks", None)
        task.progress = progress
        db.commit()

    # Advisory, up front: flag if the target would put the firewall ahead of
    # its Panorama (unsupported; breaks post-upgrade ops via Panorama). The
    # "someone forgot to upgrade Panorama first" guard — warns, doesn't block.
    _warn_if_target_exceeds_panorama(db, job, task, device)

    _set_phase(db, task, TaskPhase.PRECHECK)
    try:
        run = precheck_svc.run_precheck_for_device(
            db,
            device,
            checks=_resolve_checks_for_job(db, job),
            user_id=job.created_by_id,
        )
    except Exception as exc:  # noqa: BLE001
        if (job.pre_stage_mode or "none") in _STAGING_MODES:
            # Staging only downloads an image — a precheck that couldn't run
            # shouldn't block it. Record + proceed; if the device is genuinely
            # unreachable the download will surface that on its own.
            _record(
                db, task,
                f"Pre-check could not run ({exc}); staging job — proceeding to "
                "download anyway (prechecks are informational when staging).",
            )
            _mark_phase_done(db, task, "precheck")
            return True
        _record(db, task, f"Pre-check raised: {exc}", phase=TaskPhase.FAILED)
        _fail_job(db, job.id, f"Pre-check failed for {device.name}")
        return False

    progress = dict(task.progress or {})
    progress["precheck_run_id"] = run.id
    progress["precheck_overall"] = run.overall_severity.value
    task.progress = progress
    db.commit()

    if run.overall_severity != Severity.FAIL:
        _mark_phase_done(db, task, "precheck")
        return True

    # FAIL — record the failing checks, then either auto-ack (pre-authorized at
    # job creation) or PARK at the override gate. Parking is non-blocking: we
    # set the phase and RETURN False so the worker slot frees while the operator
    # reads the failures and decides. The /override and /rerun-check routes
    # re-dispatch drive_pair, which re-enters this function via the resume block
    # above.
    failing = _failing_check_names(run.results)
    failing_str = ", ".join(failing) if failing else "see precheck run for details"
    progress = dict(task.progress or {})
    progress["failing_checks"] = failing
    task.progress = progress
    db.commit()
    staging = (job.pre_stage_mode or "none") in _STAGING_MODES
    if job.auto_ack_precheck_failures or staging:
        why = (
            "staging job — prechecks are informational and do not gate the download"
            if staging and not job.auto_ack_precheck_failures
            else "auto_ack_precheck_failures was enabled on this job"
        )
        _record(
            db, task,
            f"Pre-check FAILED: {failing_str}. Proceeding without operator "
            f"confirmation ({why}).",
        )
        _mark_phase_done(db, task, "precheck")
        return True
    _record(
        db, task,
        f"Pre-check FAILED: {failing_str}. Job parked — click Override + proceed, "
        "Re-run check, or Abort job.",
        phase=TaskPhase.AWAITING_PRECHECK_OVERRIDE,
    )
    return False


def _phase_snapshot(db: Session, job: UpgradeJob, task: DeviceUpgradeTask, device: Device) -> bool:
    """Take the pre-upgrade snapshot and persist it.

    Snapshot failures are intentionally non-fatal — losing the diff is
    worse than skipping an upgrade, but only barely. We always record a row
    (even on failure) so the timeline shows the attempt and the post-diff
    phase has something to look up.
    """
    if _phase_already_done(task, "snapshot"):
        _record(db, task, "Snapshot already taken in a prior run; skipping")
        return True

    _set_phase(db, task, TaskPhase.SNAPSHOT)
    snap = snapshot_svc.capture(
        db,
        device,
        _client_for(db, device),
        SnapshotKind.PRE_UPGRADE,
        task_id=task.id,
    )
    if snap.error:
        _record(db, task, f"Pre-upgrade snapshot failed (non-fatal): {snap.error}")
    else:
        _record(
            db, task,
            f"Pre-upgrade snapshot captured (areas: {', '.join(sorted(snap.data.keys()))})",
        )

    progress = dict(task.progress or {})
    progress["pre_snapshot_id"] = snap.id
    task.progress = progress
    db.commit()
    _mark_phase_done(db, task, "snapshot")
    return True


def _phase_post_snapshot_and_diff(
    db: Session, job: UpgradeJob, task: DeviceUpgradeTask, device: Device
) -> bool:
    """Capture the post-upgrade snapshot and produce a diff against the pre.

    Runs after wait_for_ready + HA-resume have settled. Non-fatal in every
    failure mode: if there's no pre-snapshot to compare against (e.g. capture
    failed earlier), we still record the post-snapshot for posterity and skip
    the diff. Marker `post_snapshot` makes retries idempotent.
    """
    if _phase_already_done(task, "post_snapshot"):
        _record(db, task, "Post-upgrade snapshot already taken in a prior run; skipping")
        return True

    post = snapshot_svc.capture(
        db,
        device,
        _client_for(db, device),
        SnapshotKind.POST_UPGRADE,
        task_id=task.id,
    )
    if post.error:
        _record(db, task, f"Post-upgrade snapshot failed (non-fatal): {post.error}")
    else:
        _record(db, task, f"Post-upgrade snapshot captured")

    progress = dict(task.progress or {})
    progress["post_snapshot_id"] = post.id

    pre_id = (task.progress or {}).get("pre_snapshot_id")
    if pre_id is not None:
        pre = db.get(Snapshot, pre_id)
        if pre is not None:
            diff = snapshot_svc.compare(db, pre, post, task_id=task.id)
            if diff is not None:
                progress["snapshot_diff_id"] = diff.id
                progress["snapshot_diff_all_passed"] = diff.all_passed
                if diff.failing_areas:
                    progress["snapshot_diff_failing_areas"] = diff.failing_areas
                _record(
                    db, task,
                    f"Snapshot diff computed: "
                    + ("all areas passed" if diff.all_passed
                       else f"changes in {diff.failing_areas}"),
                )
            else:
                _record(db, task, "Snapshot diff skipped (one snapshot has no data)")

    task.progress = progress
    db.commit()
    _mark_phase_done(db, task, "post_snapshot")
    return True


def _is_transient_download_error(exc: BaseException) -> bool:
    """True for download errors that clear on their own — chiefly PAN-OS's
    "Another download is in progress. Please try again later" (the device is
    busy with a content pull or a leftover download). Worth a wait + retry
    rather than failing the device."""
    m = str(exc).lower()
    return (
        "another download is in progress" in m
        or "try again later" in m
        or "download is in progress" in m
    )


def _download_image_with_retry(
    client: PanDeviceClient, version: str, *, task: DeviceUpgradeTask, db: Session
) -> None:
    """Request a software download, wait for it, and verify it landed —
    retrying on transient "another download in progress" errors with a wait
    between attempts. Raises the last exception on permanent failure or once
    the attempts are exhausted (the caller decides how to surface it).

    SUPERSEDED in the live flow by the NON-BLOCKING `_download_step` (issue
    #185): `_phase_ensure_image` no longer calls this. Retained because its
    transient-retry semantics are pinned by test_upgrade_stage_resilience.py;
    safe to delete here once that test is migrated to `_download_step`."""
    last_exc: Exception | None = None
    for attempt in range(1, DOWNLOAD_RETRY_ATTEMPTS + 1):
        try:
            dl_job = client.request_software_download(version)
            if not dl_job:
                raise RuntimeError("download request returned no job id")
            if not _wait_for_download_job(client, dl_job, task=task, db=db):
                raise RuntimeError("download job did not finish OK")
            if not client.is_version_downloaded(version):
                raise RuntimeError("post-job software list does not show version downloaded")
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < DOWNLOAD_RETRY_ATTEMPTS and _is_transient_download_error(exc):
                _record(
                    db, task,
                    f"Download of {version} attempt {attempt}/{DOWNLOAD_RETRY_ATTEMPTS} hit a "
                    f"transient error ({exc}); the device is busy with another download — "
                    f"retrying in {DOWNLOAD_RETRY_WAIT_S}s",
                )
                time.sleep(DOWNLOAD_RETRY_WAIT_S)
                continue
            raise
    if last_exc is not None:  # pragma: no cover - loop always returns or raises
        raise last_exc


def _is_transient_install_error(exc: BaseException) -> bool:
    """True for install errors that clear on their own — chiefly PAN-OS's
    "A software install is in progress. Please try again later" (a leftover
    install, or a duplicate drive_pair racing the first). Worth a wait + retry
    rather than failing the device."""
    m = str(exc).lower()
    return (
        "a software install is in progress" in m
        or "install is in progress" in m
        or "try again later" in m
    )


def _install_with_retry(
    db: Session, task: DeviceUpgradeTask, device: Device, version: str
) -> None:
    """Request the software install and wait for the job — retrying the
    transient "a software install is in progress" error with a wait between
    attempts. Raises the last exception on permanent failure or once attempts
    are exhausted (the caller surfaces it). Rebuilds the client per attempt so a
    dropped connection during the wait doesn't poison the retry.

    SUPERSEDED in the live flow by the NON-BLOCKING `_install_step` (issue #185):
    `_phase_install_and_wait` no longer calls this. Retained because its
    transient-retry semantics are pinned by test_upgrade_dispatch_idempotency.py;
    safe to delete here once that test is migrated to `_install_step`."""
    last_exc: Exception | None = None
    for attempt in range(1, INSTALL_RETRY_ATTEMPTS + 1):
        try:
            client = _client_for(db, device)
            _set_substep(db, task, "installing")
            install_job = client.request_software_install(version)
            if not install_job:
                raise RuntimeError("install request returned no job id")
            ok = _wait_for_install_job(client, install_job, task=task, db=db)
            if not ok:
                log.info(
                    "Install job %s did not FIN cleanly; proceeding to restart anyway",
                    install_job,
                )
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < INSTALL_RETRY_ATTEMPTS and _is_transient_install_error(exc):
                _record(
                    db, task,
                    f"Install of {version} attempt {attempt}/{INSTALL_RETRY_ATTEMPTS} hit a "
                    f"transient error ({exc}); the device is busy with another install — "
                    f"retrying in {INSTALL_RETRY_WAIT_S}s",
                )
                time.sleep(INSTALL_RETRY_WAIT_S)
                continue
            raise
    if last_exc is not None:  # pragma: no cover - loop always returns or raises
        raise last_exc


def _phase_ensure_image(db: Session, job: UpgradeJob, task: DeviceUpgradeTask, device: Device) -> bool:
    """Download the target image if not already present. Reuses the stage flow
    so the user-visible Stage badge is set on the device too."""
    if _phase_already_done(task, "ensure_image"):
        _record(db, task, "Image already confirmed present in a prior run; skipping")
        return True

    # Short-circuit: device is already running the target version → no image
    # work needed. The subsequent install phase will also skip.
    if _is_already_at_target(device, job.target_version):
        _record(
            db, task,
            f"Device is already running {job.target_version}; no image download needed",
        )
        _mark_phase_done(db, task, "ensure_image")
        return True

    _set_phase(db, task, TaskPhase.DOWNLOADING_IMAGE)
    try:
        client = _client_for(db, device)
        if client.is_version_downloaded(job.target_version):
            _mark_phase_done(db, task, "ensure_image")
            return True
    except Exception as exc:  # noqa: BLE001
        _record(db, task, f"Couldn't query software list: {exc}", phase=TaskPhase.FAILED)
        _fail_job(db, job.id, f"Image presence check failed for {device.name}")
        return False

    # Major-version jump (crosses the X.Y feature train, e.g. 11.2 -> 12.1):
    # PAN-OS won't install a maintenance release of the new train until that
    # train's BASE image is downloaded first. The base is NOT always X.Y.0
    # (12.1's base is 12.1.2), so read it from the firewall's software list
    # (release-type == "Base") rather than guessing. Same-train maintenance
    # bumps (11.2.4 -> 11.2.11) skip this entirely — no wasted request.
    #
    # NON-BLOCKING (issue #185): the base and target downloads each go through
    # _download_step, which issues once and polls the PAN-OS job across
    # re-dispatches. On a PARKED result we return False to free the slot; the
    # re-dispatch re-enters here and _download_step's is_version_downloaded
    # short-circuit skips any download that already completed, so we resume at
    # whichever image is still in flight.
    if _is_major_jump(device.current_version, job.target_version):
        try:
            software = client.list_software()
            # #175 made list_software() local-only by default (check_updates=False).
            # On a FRESH major jump the device has no image from the target train
            # yet, so the base image isn't in the local list and
            # base_image_from_list() returns None — which silently skips the base
            # download, and the maintenance release then downloads but never
            # "lands" (PAN-OS won't surface it until its train's base is present).
            # Re-query WITH the update-server list so the base (and its
            # release_type marker) is discoverable. Local-first keeps this cheap
            # once the base is downloaded (the train shows up locally and we skip
            # the update-server round-trip on subsequent poll re-entries).
            if base_image_from_list(job.target_version, software) is None:
                software = client.list_software(check_updates=True)
        except Exception as exc:  # noqa: BLE001
            software = []
            _record(db, task, f"Could not read software list for base-image check: {exc}")
        base = base_image_from_list(job.target_version, software)
        if base and base != job.target_version:
            if client.is_version_downloaded(base):
                _record(db, task, f"Base image {base} already present; no base download needed")
            else:
                _record(
                    db, task,
                    f"Major-version jump to {job.target_version}: downloading base image "
                    f"{base} first (required before the maintenance release will install)",
                )
                r = _download_step(db, job, task, device, base, slot="base")
                if r is _Wait.PARKED:
                    return False
                if r is _Wait.TIMED_OUT:
                    # _download_step recorded FAILED + _fail_job on a permanent
                    # failure; the job-timeout path lands here too.
                    if task.phase != TaskPhase.FAILED:
                        _record(
                            db, task,
                            f"Base image {base} download did not finish within "
                            f"{INSTALL_JOB_TIMEOUT_S}s",
                            phase=TaskPhase.FAILED,
                        )
                        _fail_job(db, job.id, f"Base image {base} download failed for {device.name}")
                    return False
        elif not base:
            _record(
                db, task,
                f"Major-version jump, but couldn't determine the base image for "
                f"{job.target_version} from the software list; proceeding (PAN-OS will "
                f"require it if needed)",
            )

    # Target image. Same non-blocking issue/poll split as the base above.
    r = _download_step(db, job, task, device, job.target_version, slot="target")
    if r is _Wait.PARKED:
        return False
    if r is _Wait.TIMED_OUT:
        if task.phase != TaskPhase.FAILED:
            _record(
                db, task,
                f"Image download did not finish within {INSTALL_JOB_TIMEOUT_S}s",
                phase=TaskPhase.FAILED,
            )
            _fail_job(db, job.id, f"Image download failed for {device.name}")
        return False

    # Reflect on the Device for the staged badge.
    device.staged_version = job.target_version
    device.staged_at = datetime.now(timezone.utc)
    device.staged_error = None
    db.commit()
    _mark_phase_done(db, task, "ensure_image")
    return True


def _phase_suspend_ha(db: Session, job: UpgradeJob, task: DeviceUpgradeTask, device: Device) -> bool:
    if _phase_already_done(task, "suspend_ha"):
        _record(db, task, "HA suspend already done in a prior run; skipping")
        return True

    # If the device is already running the target version we don't need to
    # touch its HA state at all — install will be skipped downstream.
    if _is_already_at_target(device, job.target_version):
        _record(db, task, "Device already at target version — no HA suspend needed")
        _mark_phase_done(db, task, "suspend_ha")
        return True

    _set_phase(db, task, TaskPhase.SUSPEND_SECONDARY)
    if not _ha_op_with_retry(db, task, device, "HA suspend", lambda c: c.suspend_ha()):
        _record(db, task, "HA suspend exhausted retries", phase=TaskPhase.FAILED)
        _fail_job(db, job.id, f"HA suspend failed for {device.name}")
        return False
    _mark_phase_done(db, task, "suspend_ha")
    return True


def _progress_get(task: DeviceUpgradeTask, key: str, default=None):
    return (task.progress or {}).get(key, default)


def _progress_set(db: Session, task: DeviceUpgradeTask, **kv) -> None:
    """Set one or more identifier-named keys on task.progress (None value deletes
    the key). For keys that aren't valid Python identifiers (the ``::``-namespaced
    wait keys) use ``_progress_put``. Dict-copy + reassign + commit (plain-JSON
    column — see _mark_phase_done)."""
    _progress_put_many(db, task, kv)


def _progress_put(db: Session, task: DeviceUpgradeTask, key: str, value) -> None:
    """Single-key form of _progress_set that accepts ANY string key (e.g.
    ``download_job_id::base``). None value deletes the key."""
    _progress_put_many(db, task, {key: value})


def _progress_put_many(db: Session, task: DeviceUpgradeTask, kv: dict) -> None:
    progress = dict(task.progress or {})
    changed = False
    for key, value in kv.items():
        if value is None:
            if key in progress:
                progress.pop(key, None)
                changed = True
        elif progress.get(key) != value:
            progress[key] = value
            changed = True
    if changed:
        task.progress = progress
        db.commit()


def _install_step(
    db: Session, job: UpgradeJob, task: DeviceUpgradeTask, device: Device
) -> _Wait:
    """NON-BLOCKING install: issue `request system software install` exactly ONCE
    (behind the `install_issued` marker + persisted `install_job_id`), then poll
    the PAN-OS job to FIN across re-dispatches instead of blocking in a loop.

    Replaces the live use of the blocking `_install_with_retry` /
    `_wait_for_install_job` (kept only for their isolated unit tests). The install
    command itself returns quickly; the multi-minute part is the JOB completing,
    which `_await_or_park` now polls one tick at a time so the worker slot frees.

    Idempotency: on every re-entry, if `install_job_id` is already persisted we
    NEVER re-issue — we only CHECK that job's status. We re-issue solely on the
    deliberate transient-retry path (PAN-OS "a software install is in progress"),
    which clears the handle + markers and re-dispatches, bounded by a persisted
    `install_attempt` counter against INSTALL_RETRY_ATTEMPTS.

    Returns DONE (software_installed marked), PARKED (re-dispatched — caller
    returns False), or TIMED_OUT (caller fails the task).
    """
    # Phase 1: issue the install once. No persisted job id AND not yet issued →
    # this is the first attempt (or a re-issue after a transient clear).
    if _progress_get(task, "install_job_id") is None:
        attempt = int(_progress_get(task, "install_attempt", 0)) + 1
        _progress_set(db, task, install_attempt=attempt)
        try:
            client = _client_for(db, device)
            _set_substep(db, task, "installing")
            install_job = client.request_software_install(job.target_version)
            if not install_job:
                raise RuntimeError("install request returned no job id")
        except Exception as exc:  # noqa: BLE001
            # Transient "a software install is in progress" (a leftover install,
            # or a duplicate dispatch racing us): wait + re-issue, exactly like
            # the old blocking retry — but by parking (re-dispatch) rather than
            # sleeping in the slot. Bounded by INSTALL_RETRY_ATTEMPTS.
            if attempt < INSTALL_RETRY_ATTEMPTS and _is_transient_install_error(exc):
                _record(
                    db, task,
                    f"Install of {job.target_version} attempt {attempt}/"
                    f"{INSTALL_RETRY_ATTEMPTS} hit a transient error ({exc}); the "
                    f"device is busy with another install — retrying in "
                    f"{INSTALL_RETRY_WAIT_S}s",
                )
                # Nothing to clear (no job id was set); just re-dispatch.
                drive_pair_task.apply_async(
                    (task.job_id, task.ha_pair_key), countdown=INSTALL_RETRY_WAIT_S
                )
                return _Wait.PARKED
            # Permanent failure, or transient but out of attempts: fail the task.
            _record(db, task, f"Install failed: {exc}", phase=TaskPhase.FAILED)
            _fail_job(db, job.id, f"Install failed for {device.name}")
            return _Wait.TIMED_OUT
        # Issued cleanly — persist the handle so re-entries poll (never re-issue).
        _progress_set(db, task, install_job_id=install_job)
        _mark_phase_done(db, task, "install_issued")
        _record(db, task, f"Install requested (PAN-OS job {install_job}); awaiting completion")

    # Phase 2: poll the persisted job to FIN, one tick per re-dispatch.
    job_id = _progress_get(task, "install_job_id")

    def _install_finished() -> bool:
        status = _client_for(db, device).get_job_status(job_id)
        _set_job_progress(db, task, "install_progress", status.get("progress"))
        if status.get("status") != "FIN":
            return False
        # FIN. Like the old _install_with_retry: a non-OK result is NOT fatal
        # here — the device still reboots, and the post-reboot
        # _verify_install_applied (#186) is the real backstop against a
        # non-upgrade. Persist the result so the DONE branch below can log it.
        _progress_set(db, task, install_result=status.get("result") or "unknown")
        return True

    res = _await_or_park(
        db, task,
        marker="install_job",
        timeout_s=INSTALL_JOB_TIMEOUT_S,
        is_done=_install_finished,
    )
    if res is _Wait.DONE:
        result = _progress_get(task, "install_result")
        if result == "OK":
            _record(db, task, "Install complete")
        else:
            log.info(
                "Install job %s did not FIN cleanly (result=%s); proceeding to restart anyway",
                job_id, result,
            )
            _record(
                db, task,
                f"Install job finished with result '{result or 'unknown'}'; "
                "proceeding to reboot (post-reboot version is verified before completion)",
            )
        # Clear the issue handle/marker + scratch so a future install (e.g. the
        # partner, or a reconcile that cleared software_installed) re-issues
        # cleanly rather than polling this now-finished job id.
        _progress_set(db, task, install_job_id=None, install_attempt=None, install_result=None)
        _unmark_phase(db, task, "install_issued")
        _mark_phase_done(db, task, "software_installed")
    return res


def _reboot_step(
    db: Session, job: UpgradeJob, task: DeviceUpgradeTask, device: Device
) -> _Wait:
    """NON-BLOCKING reboot: issue `request restart system` exactly ONCE (behind
    the `reboot_issued` marker), then confirm the device is back on the target
    version across re-dispatches instead of blocking in `wait_for_ready`.

    A re-entry must NEVER re-restart a device that's already rebooting — that's
    what the `reboot_issued` marker guards. We replace the blocking
    `client.wait_for_ready(...)` (which required N consecutive OK probes before
    declaring the mgmt plane stable) with a single-shot readiness check per
    re-dispatch PLUS a consecutive-success counter persisted on progress
    (`reboot_ok_streak`): the device must answer get_system_info AND report the
    target version on REBOOT_READY_CONSECUTIVE successive ticks before we call it
    up. Re-implementing the streak is what stops a flaky mgmt response mid-reboot
    (the API briefly answers, then the box goes back down as other subsystems
    init) from being misread as "up".

    Returns DONE (device confirmed up on target), PARKED, or TIMED_OUT.
    """
    if not _phase_already_done(task, "reboot_issued"):
        try:
            client = _client_for(db, device)
            _set_substep(db, task, "rebooting")
            _record(db, task, "Issuing system restart")
            client.restart_system()
        except Exception as exc:  # noqa: BLE001
            _record(db, task, f"Reboot request failed: {exc}", phase=TaskPhase.FAILED)
            _fail_job(db, job.id, f"Reboot request failed for {device.name}")
            return _Wait.TIMED_OUT
        _mark_phase_done(db, task, "reboot_issued")
        _progress_set(db, task, reboot_ok_streak=0)
        _record(
            db, task,
            f"Waiting for device to come back online on {job.target_version} "
            f"(up to {REBOOT_TIMEOUT_S}s, needs {REBOOT_READY_CONSECUTIVE} "
            f"consecutive healthy checks)",
        )

    _set_substep(db, task, "rebooting")

    def _reboot_ready() -> bool:
        # One readiness probe. Reachable AND running the target version.
        # Rebuild the client each tick — the socket was torn down by the reboot.
        info = _client_for(db, device).get_system_info()
        version = (getattr(info, "sw_version", None) or "").strip()
        on_target = version != "" and version == (job.target_version or "").strip()
        streak = int(_progress_get(task, "reboot_ok_streak", 0))
        if on_target:
            streak += 1
        else:
            # Any miss (unreachable raises; reachable-but-wrong-version lands
            # here) resets the streak — the device must be consistently up on the
            # new version, not flapping or still on the old image mid-reboot.
            streak = 0
        _progress_set(db, task, reboot_ok_streak=streak)
        return streak >= REBOOT_READY_CONSECUTIVE

    res = _await_or_park(
        db, task,
        marker="reboot_ready",
        timeout_s=REBOOT_TIMEOUT_S,
        is_done=_reboot_ready,
    )
    if res is _Wait.DONE:
        _record(db, task, "Device mgmt plane is stable")
        _progress_set(db, task, reboot_ok_streak=None)
    return res


def _download_step(
    db: Session,
    job: UpgradeJob,
    task: DeviceUpgradeTask,
    device: Device,
    version: str,
    *,
    slot: str,
) -> _Wait:
    """NON-BLOCKING image download: issue `request system software download`
    exactly ONCE (behind per-``slot`` handles) and poll the PAN-OS job to FIN
    across re-dispatches, instead of blocking ~5 min in _wait_for_download_job.

    ``slot`` namespaces the persisted handles so a major-version jump can stage
    the BASE image (slot="base") and the TARGET image (slot="target")
    independently — a re-entry resumes whichever is in flight without colliding.

    Idempotency: the authoritative "already done" check is
    ``client.is_version_downloaded(version)`` (run first on every entry). With a
    job in flight we only CHECK its status — never re-issue — except on the
    deliberate transient-retry path ("another download in progress"), bounded by
    a persisted per-slot attempt counter against DOWNLOAD_RETRY_ATTEMPTS.

    Returns DONE (image present), PARKED (re-dispatched — caller returns False),
    or TIMED_OUT (the step recorded FAILED + _fail_job; caller returns False).
    """
    job_key = f"download_job_id::{slot}"
    attempt_key = f"download_attempt::{slot}"
    issued_marker = f"download_issued::{slot}"

    def _present() -> bool:
        try:
            return _client_for(db, device).is_version_downloaded(version)
        except Exception as exc:  # noqa: BLE001
            log.info("download presence check for %s failed: %s", version, exc)
            return False

    # Authoritative short-circuit: if the image is already on the box, this
    # download is done regardless of any stale handles — clear them and finish.
    if _present():
        _progress_put(db, task, job_key, None)
        _unmark_phase(db, task, issued_marker)
        return _Wait.DONE

    progress_pct_key = "download_progress"

    # Phase 1: issue the download once (no persisted job id for this slot).
    if _progress_get(task, job_key) is None:
        attempt = int(_progress_get(task, attempt_key, 0)) + 1
        _progress_put(db, task, attempt_key, attempt)
        try:
            dl_job = _client_for(db, device).request_software_download(version)
            if not dl_job:
                raise RuntimeError("download request returned no job id")
        except Exception as exc:  # noqa: BLE001
            if attempt < DOWNLOAD_RETRY_ATTEMPTS and _is_transient_download_error(exc):
                _record(
                    db, task,
                    f"Download of {version} attempt {attempt}/{DOWNLOAD_RETRY_ATTEMPTS} "
                    f"hit a transient error ({exc}); the device is busy with another "
                    f"download — retrying in {DOWNLOAD_RETRY_WAIT_S}s",
                )
                drive_pair_task.apply_async(
                    (task.job_id, task.ha_pair_key), countdown=DOWNLOAD_RETRY_WAIT_S
                )
                return _Wait.PARKED
            _record(db, task, f"Image download failed: {exc}", phase=TaskPhase.FAILED)
            _fail_job(db, job.id, f"Image download failed for {device.name}")
            return _Wait.TIMED_OUT
        _progress_put(db, task, job_key, dl_job)
        _mark_phase_done(db, task, issued_marker)
        _record(db, task, f"Download of {version} requested (PAN-OS job {dl_job}); awaiting completion")

    # Phase 2: poll the persisted job to FIN, one tick per re-dispatch.
    job_id = _progress_get(task, job_key)

    def _download_finished() -> bool:
        status = _client_for(db, device).get_job_status(job_id)
        _set_job_progress(db, task, progress_pct_key, status.get("progress"))
        return status.get("status") == "FIN"

    res = _await_or_park(
        db, task,
        marker=f"download_job::{slot}",
        timeout_s=INSTALL_JOB_TIMEOUT_S,  # generous; reused for downloads (as before)
        is_done=_download_finished,
    )
    if res is _Wait.DONE:
        # Job FIN — verify the image actually landed in the software list. If it
        # didn't, drop the handle so the next entry re-issues (bounded by the
        # attempt counter), mirroring the old retry loop's re-request behavior.
        if _present():
            _progress_put(db, task, job_key, None)
            _unmark_phase(db, task, issued_marker)
            _record(db, task, f"Image {version} downloaded")
            return _Wait.DONE
        attempt = int(_progress_get(task, attempt_key, 0))
        _progress_put(db, task, job_key, None)
        _unmark_phase(db, task, issued_marker)
        if attempt < DOWNLOAD_RETRY_ATTEMPTS:
            _record(
                db, task,
                f"Download job for {version} finished but the software list does not "
                f"show it (attempt {attempt}/{DOWNLOAD_RETRY_ATTEMPTS}); re-requesting",
            )
            drive_pair_task.apply_async(
                (task.job_id, task.ha_pair_key), countdown=DOWNLOAD_RETRY_WAIT_S
            )
            return _Wait.PARKED
        _record(
            db, task,
            f"Download of {version} did not land after {DOWNLOAD_RETRY_ATTEMPTS} attempts "
            f"(download finished but the image never appeared on the device). Most likely "
            f"the device is low on disk — free space (or run disk cleanup) and retry.",
            phase=TaskPhase.FAILED,
        )
        _fail_job(db, job.id, f"Image download failed for {device.name}")
        return _Wait.TIMED_OUT
    return res


# Acceptable HA end-states for the upgraded member to have rejoined the cluster:
# either is safe to then suspend the OTHER peer for failover. (Same set the old
# blocking _wait_for_passive used.)
_PASSIVE_SAFE_STATES = frozenset({"passive", "active-secondary"})


def _passive_step(
    db: Session, job: UpgradeJob, task: DeviceUpgradeTask, device: Device
) -> _Wait:
    """NON-BLOCKING HA-resync wait: single-shot check of the upgraded member's HA
    state per re-dispatch, instead of blocking ~minutes in _wait_for_passive.

    The pair flow must NOT advance to failover/primary-upgrade with this device
    only half-rejoined, so after resume we wait until it reports a safe rejoined
    state (`passive` / `active-secondary`). Not safe yet → park; safe → DONE.

    Mirrors _wait_for_passive's observability — records each distinct state seen
    and keeps device.ha_state fresh — and, on TIMED_OUT, stashes the SAME
    `wait_for_passive_diagnostics` block on progress so _format_passive_timeout_message
    renders the rich failure message unchanged. State history accumulates across
    re-dispatches in progress (`_passive_states_seen`) since each invocation is a
    fresh process.
    """
    def _is_passive() -> bool:
        info = _client_for(db, device).get_system_info()
        state = (getattr(info, "ha_state", None) or "").lower().strip()
        last = _progress_get(task, "_passive_last_state")
        if state != last:
            _record(db, task, f"HA state observed: '{state or 'unknown'}'")
            seen = list(_progress_get(task, "_passive_states_seen", []))
            seen.append(state or "unknown")
            _progress_put_many(
                db, task,
                {"_passive_last_state": state, "_passive_states_seen": seen},
            )
            # Keep the device row fresh so other watchers see progress.
            device.ha_state = getattr(info, "ha_state", None)
            db.commit()
        return state in _PASSIVE_SAFE_STATES

    res = _await_or_park(
        db, task,
        marker="wait_for_passive",
        timeout_s=HA_PASSIVE_TIMEOUT_S,
        is_done=_is_passive,
        poll_s=HA_PASSIVE_POLL_S,
    )
    if res is _Wait.TIMED_OUT:
        # Stash diagnostics in the SAME shape _wait_for_passive used so
        # _format_passive_timeout_message keeps working untouched.
        states_seen = list(_progress_get(task, "_passive_states_seen", []))
        progress = dict(task.progress or {})
        progress["wait_for_passive_diagnostics"] = {
            "last_state": _progress_get(task, "_passive_last_state") or "unknown",
            "states_seen": states_seen,
            "polls": len(states_seen),
            "timeout_s": HA_PASSIVE_TIMEOUT_S,
        }
        # Clear the per-run scratch keys now that the diagnostics are captured.
        progress.pop("_passive_last_state", None)
        progress.pop("_passive_states_seen", None)
        task.progress = progress
        db.commit()
    elif res is _Wait.DONE:
        _progress_put_many(
            db, task, {"_passive_last_state": None, "_passive_states_seen": None}
        )
    return res


def _phase_install_and_wait(
    db: Session,
    job: UpgradeJob,
    task: DeviceUpgradeTask,
    device: Device,
    *,
    started_phase: TaskPhase = TaskPhase.UPGRADE_PRIMARY,
) -> bool:
    """Full install sequence with two optional pauses:

      1. request system software install   (does NOT reboot the device)
      2. poll the install job until FIN
      3. [gate] if not job.auto_reboot_after_install: park at
         AWAITING_REBOOT_CONFIRM until the operator clicks "Reboot now"
      4. request restart system
      5. wait_for_ready until the mgmt plane returns
      6. if HA-paired, resume HA so it rejoins the cluster
      7. if HA-paired, WAIT until the device is in 'passive' state — the
         pair flow must NOT advance to failover/primary upgrade with this
         device only half-rejoined
      8. refresh runtime state so the badge reflects the new version

    Idempotent: the install+reboot block is skipped when the device is
    already at target, and the HA resume block always runs (resume_ha is a
    no-op on already-functional devices, wait_for_passive returns
    immediately when state is already passive). This means retrying a job
    that died partway through correctly resumes from where it stopped:
    if install succeeded but HA resume failed, the retry just redoes the
    HA resume block.
    """
    install_needed = (
        not _phase_already_done(task, "install_complete")
        and not _is_already_at_target(device, job.target_version)
    )

    if install_needed:
        _set_phase(db, task, started_phase)
        # Step 1+2: install — NON-BLOCKING (issue #185). Guarded by its own
        # "software_installed" marker so that parking (at the reboot gate, or
        # while the install job runs) doesn't re-issue the install on a
        # re-dispatch. _install_step issues `request software install` exactly
        # once (persisting install_job_id) and polls the PAN-OS job to FIN one
        # tick per re-dispatch — the worker slot frees the whole time instead of
        # blocking ~5 min in _wait_for_install_job.
        if not _phase_already_done(task, "software_installed"):
            r = _install_step(db, job, task, device)
            if r is _Wait.PARKED:
                return False  # re-dispatched; free the slot until then
            if r is _Wait.TIMED_OUT:
                # _install_step already recorded FAILED + _fail_job on permanent
                # failure / exhausted retries. The install JOB exceeding its own
                # timeout falls here too — surface it explicitly.
                if task.phase != TaskPhase.FAILED:
                    _record(
                        db, task,
                        f"Install job did not finish within {INSTALL_JOB_TIMEOUT_S}s",
                        phase=TaskPhase.FAILED,
                    )
                    _fail_job(db, job.id, f"Install job timed out for {device.name}")
                return False
            # DONE — software_installed is marked; fall through to the reboot.

        # Step 3: optional pause before reboot. Default behavior — the operator
        # explicitly clicks Reboot now. Non-blocking: _confirm_gate parks at
        # AWAITING_REBOOT_CONFIRM and RETURNS so the slot frees until /confirm
        # re-dispatches; the "reboot_confirmed" marker keeps us past the gate on
        # any later re-dispatch.
        if not job.auto_reboot_after_install:
            _set_substep(db, task, None)
            if not _confirm_gate(
                db, task,
                TaskPhase.AWAITING_REBOOT_CONFIRM,
                "reboot_confirmed",
                parked_msg="Install done; awaiting operator confirmation to reboot.",
                proceed_msg="Reboot confirmed; restarting now.",
            ):
                return False
            _set_phase(db, task, started_phase)

        # Step 4+5: restart and wait for the mgmt plane — NON-BLOCKING (issue
        # #185). _reboot_step issues `restart system` exactly once (behind the
        # reboot_issued marker — a re-entry must NEVER re-restart) and confirms
        # readiness (reachable AND on target, N consecutive ticks) one tick per
        # re-dispatch, instead of blocking ~10 min in wait_for_ready.
        r = _reboot_step(db, job, task, device)
        if r is _Wait.PARKED:
            return False
        if r is _Wait.TIMED_OUT:
            if task.phase != TaskPhase.FAILED:
                _record(
                    db, task,
                    f"Device did not come back online within {REBOOT_TIMEOUT_S}s "
                    f"after reboot (need {REBOOT_READY_CONSECUTIVE} consecutive "
                    f"healthy checks on {job.target_version})",
                    phase=TaskPhase.FAILED,
                )
                _fail_job(db, job.id, f"Reboot/wait failed for {device.name}")
            return False

        try:
            _set_substep(db, task, "verifying")
            precheck_svc.probe_device(db, device, client=_client_for(db, device))
            db.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("Post-reboot probe failed for %s: %s", device.name, exc)

        # Guard against a silent non-upgrade (issue #186): the device must be
        # RUNNING the target now, or we fail it (retryable) instead of marking
        # it done and marching on as if it upgraded. _reboot_step already
        # confirmed the target version via consecutive healthy checks, but the
        # probe above refreshes the row this reads — keep the guard as the
        # explicit backstop (and for the install-already-done re-entry path).
        if not _verify_install_applied(db, job, task, device):
            return False

        # Reboot is fully confirmed — drop its issue marker so a future
        # install_complete clear (reconcile) re-runs the reboot cleanly.
        _unmark_phase(db, task, "reboot_issued")
        _mark_phase_done(db, task, "install_complete")
    elif _phase_already_done(task, "install_complete"):
        _record(db, task, "Install + reboot already completed in a prior run; skipping")
    else:
        _record(
            db, task,
            f"Device is already running {job.target_version}; skipping install/reboot",
        )
        _mark_phase_done(db, task, "install_complete")

    # HA resume block. Tracked under its own marker ("ha_resume_complete")
    # so a retry of a job that died here will redo *just* this part —
    # not the install/reboot above. resume_ha is a no-op on an
    # already-functional device; wait_for_passive returns immediately
    # when the state is already passive. So even when this block re-runs
    # against a healthy device, it's cheap and safe.
    if device.ha_peer_id is not None and not _phase_already_done(task, "ha_resume_complete"):
        # Issue-once guard for the resume itself. wait_for_passive below is
        # NON-BLOCKING (issue #185) and parks between checks; on each re-dispatch
        # we re-enter this whole block, so without this marker we'd re-run the
        # subsystem-ready wait AND re-issue resume_ha every 30s while waiting for
        # 'passive'. The subsystem-ready + resume + verify steps stay BLOCKING
        # (seconds-to-low-minutes, and intentionally not converted — see #185),
        # so we run them exactly once behind "ha_resumed" and then only re-poll
        # the passive state on subsequent entries. Reconcile clears ha_resumed
        # alongside ha_resume_complete so a Retry that genuinely needs to
        # re-resume (device fell back to suspended) does re-issue.
        if not _phase_already_done(task, "ha_resumed"):
            _set_substep(db, task, "resuming_ha")
            _record(
                db, task,
                "Waiting for HA subsystem readiness before issuing resume"
                f" (up to {HA_SUBSYS_TIMEOUT_S}s)",
            )
            if not _wait_for_ha_subsystem_ready(db, task, device):
                _record(
                    db, task,
                    f"HA subsystem never became ready in {HA_SUBSYS_TIMEOUT_S}s",
                    phase=TaskPhase.FAILED,
                )
                _fail_job(db, job.id, f"HA subsystem stuck on {device.name}")
                return False

            _record(db, task, "Issuing HA resume")
            if not _ha_op_with_retry(db, task, device, "HA resume", lambda c: c.resume_ha()):
                _record(db, task, "HA resume exhausted retries", phase=TaskPhase.FAILED)
                _fail_job(db, job.id, f"HA resume failed for {device.name}")
                return False

            # ---- Post-resume verification ----
            # The resume_ha API call frequently returns success even when
            # PAN-OS's HA daemon ignores it (e.g. content version mismatch
            # with the peer, hold-down timer not elapsed, peer still rebooting).
            # If we go straight to wait_for_passive after that silent failure,
            # we burn the full 10-minute timeout watching 'suspended' before
            # giving up. Re-issue resume_ha up to 3 times, 30s apart, until
            # the state moves off 'suspended' — or surface a clean failure.
            if not _verify_resume_took_effect(db, task, device):
                _record(
                    db, task,
                    "HA resume returned success but the device stayed suspended "
                    "after repeated re-issues — the HA daemon is rejecting the "
                    "command. Common causes: content-version mismatch with the "
                    "peer, HA hold-down timer, or the peer is still rebooting. "
                    "Run `show high-availability state` on the device.",
                    phase=TaskPhase.FAILED,
                )
                _fail_job(db, job.id, f"HA resume was silently ignored on {device.name}")
                return False
            _mark_phase_done(db, task, "ha_resumed")

        # Wait for the upgraded member to rejoin as 'passive' — NON-BLOCKING.
        # _passive_step checks the HA state once per re-dispatch and parks until
        # it settles, instead of blocking ~minutes in _wait_for_passive.
        _set_substep(db, task, "waiting_for_passive")
        _record(db, task, "Waiting for HA state to settle to 'passive'")
        r = _passive_step(db, job, task, device)
        if r is _Wait.PARKED:
            return False
        if r is _Wait.TIMED_OUT:
            _record(
                db, task,
                _format_passive_timeout_message(db, task, device),
                phase=TaskPhase.FAILED,
            )
            _fail_job(db, job.id, f"HA never reached 'passive' for {device.name}")
            return False
        _record(db, task, "HA state confirmed 'passive'")
        # Resume fully confirmed — drop the inner issue marker so a reconcile
        # that clears ha_resume_complete also forces a fresh resume on retry.
        _unmark_phase(db, task, "ha_resumed")
        _mark_phase_done(db, task, "ha_resume_complete")
    elif device.ha_peer_id is not None:
        _record(db, task, "HA resume already completed in a prior run; skipping")

    return True


def _phase_postcheck(
    db: Session,
    job: UpgradeJob,
    task: DeviceUpgradeTask,
    device: Device,
    *,
    finishing_phase: TaskPhase = TaskPhase.POSTCHECK_PRIMARY,
) -> bool:
    if _phase_already_done(task, "postcheck"):
        _record(db, task, "Post-check already completed in a prior run; skipping")
        return True

    # Resume from a non-blocking override park (see _phase_precheck for the full
    # rationale) — handle the operator's Override / Re-run / Abort here before
    # re-running anything.
    if task.phase == TaskPhase.AWAITING_POSTCHECK_OVERRIDE:
        action = _resolve_override_action(db, task)
        if action is None or action == _OverrideOutcome.ABORT:
            return False
        if action == _OverrideOutcome.PROCEED:
            _record(db, task, "Operator overrode post-check failure; proceeding.")
            _mark_phase_done(db, task, "postcheck")
            return True
        # RERUN — drop stale results and fall through to execute fresh.
        progress = dict(task.progress or {})
        progress.pop("failing_postchecks", None)
        task.progress = progress
        db.commit()

    # Enter the postcheck phase now (not in the caller) so the badge reflects
    # it through the reachability wait below — AND so the resume block above
    # sees the still-parked AWAITING_POSTCHECK_OVERRIDE phase on re-entry
    # rather than a phase the caller clobbered before this function ran.
    _set_phase(db, task, finishing_phase)

    # Post-reboot reconnect window. The device's own mgmt plane is back
    # (wait_for_ready cleared) and HA has resumed, but the managing Panorama
    # can lag in re-establishing its connection — and the postcheck runs
    # through the upgrade route (Panorama-proxied by default). Firing it
    # during that window gives a "Panorama unreachable" runner error and a
    # postcheck that validated nothing (job #4: postcheck errored, yet the
    # post_snapshot moments later succeeded — the route had recovered in
    # between). Wait for the route to actually answer before running.
    _set_substep(db, task, "awaiting_reachability")
    _record(
        db, task,
        "Waiting for the device to be reachable via its upgrade route before "
        "post-check (the managing Panorama may still be re-registering it after "
        f"the reboot; up to {POSTCHECK_ROUTE_TIMEOUT_S}s)",
    )
    if _wait_for_upgrade_route_ready(db, device):
        _record(db, task, "Device reachable via its upgrade route; running post-check")
    else:
        _record(
            db, task,
            "Device still not reachable via its upgrade route after waiting; "
            "running post-check anyway — if it reports unreachable, confirm "
            "Panorama shows the device connected, then Re-run the check.",
        )
    _set_substep(db, task, None)

    try:
        run = precheck_svc.run_precheck_for_device(
            db,
            device,
            checks=_resolve_checks_for_job(db, job),
            user_id=job.created_by_id,
        )
    except Exception as exc:  # noqa: BLE001
        _record(db, task, f"Post-check raised: {exc}", phase=TaskPhase.FAILED)
        _fail_job(db, job.id, f"Post-check failed for {device.name}")
        return False

    progress = dict(task.progress or {})
    progress["postcheck_run_id"] = run.id
    progress["postcheck_overall"] = run.overall_severity.value
    task.progress = progress
    db.commit()

    if run.overall_severity != Severity.FAIL:
        _mark_phase_done(db, task, "postcheck")
        return True

    # FAIL — record, then auto-ack (pre-authorized) or PARK (non-blocking) at
    # the override gate. Distinguish "ran and some checks failed" from "couldn't
    # run at all" (runner errored before evaluating anything: 0 results + an
    # error, e.g. unreachable through Panorama) in the timeline message.
    failing = _failing_check_names(run.results)
    if not run.results and run.error:
        detail = f"Post-check could not run: {run.error}"
    else:
        detail = (
            "Post-check FAILED: "
            f"{', '.join(failing) if failing else 'see post-check run for details'}"
        )
    progress = dict(task.progress or {})
    progress["failing_postchecks"] = failing
    task.progress = progress
    db.commit()
    if job.auto_ack_postcheck_failures:
        _record(
            db, task,
            f"{detail}. Failures auto-acknowledged (auto_ack_postcheck_failures "
            "was enabled on this job); proceeding without operator confirmation",
        )
        _mark_phase_done(db, task, "postcheck")
        return True
    _record(
        db, task,
        f"{detail}. Job parked — click Override + proceed, Re-run check, or Abort job.",
        phase=TaskPhase.AWAITING_POSTCHECK_OVERRIDE,
    )
    return False


def _phase_failover(
    db: Session,
    job: UpgradeJob,
    active_task: DeviceUpgradeTask,
    passive_task: DeviceUpgradeTask,
) -> bool:
    """Suspend the current ACTIVE so the upgraded passive takes over.

    SAFETY: refuse to suspend the active unless the upgraded peer is back in
    HA in a state that can actually take over. Suspending the active while
    the peer is still suspended/non-functional == outage.
    """
    # Failover is tracked on the passive_task — it's the "I became active"
    # transition for that task. A retry that finds this already done skips
    # re-toggling (running it again would just bounce roles).
    if _phase_already_done(passive_task, "failover"):
        _record(db, passive_task, "Failover already completed in a prior run; skipping")
        return True

    # Probe the passive (just-upgraded) member to get its CURRENT HA state.
    try:
        precheck_svc.probe_device(db, passive_task.device, client=_client_for(db, passive_task.device))
        db.commit()
    except Exception as exc:  # noqa: BLE001
        _record(db, passive_task, f"Pre-failover probe of peer failed: {exc}", phase=TaskPhase.FAILED)
        _fail_job(db, job.id, f"Could not verify peer state before failover")
        return False

    db.refresh(passive_task.device)
    peer_state = (passive_task.device.ha_state or "").lower().strip()
    # Acceptable states for the peer to be "ready to take over": it should
    # already be participating in HA (passive or active-secondary). If it's
    # 'suspended', 'non-functional', 'initial', 'tentative', etc. — STOP.
    safe_states = {"passive", "active-secondary", "active"}
    if peer_state not in safe_states:
        msg = (
            f"REFUSING to failover: peer ({passive_task.device.name}) HA state is "
            f"'{peer_state or 'unknown'}', not one of {sorted(safe_states)}. "
            f"Suspending the active member now would take both firewalls offline. "
            f"Investigate the peer (likely needs HA resume or further recovery) "
            f"before retrying."
        )
        _record(db, passive_task, msg, phase=TaskPhase.FAILED)
        _record(db, active_task, "Failover aborted (see peer task)", phase=TaskPhase.FAILED)
        _fail_job(db, job.id, "Failover safety check failed")
        return False

    if not _ha_op_with_retry(
        db, passive_task, active_task.device, "Failover (suspend active)",
        lambda c: c.trigger_failover(),
    ):
        _record(db, passive_task, "Failover failed after retries", phase=TaskPhase.FAILED)
        _fail_job(db, job.id, "Failover failed")
        return False
    time.sleep(FAILOVER_SETTLE_S)
    _mark_phase_done(db, passive_task, "failover")
    return True


# ---------- helpers ----------


def _client_for(db: Session, device: Device) -> PanDeviceClient:
    """Build a PanDeviceClient honoring the proxy-by-default policy.

    Threads the session through to `build_client_with_fallback` so the
    builder can read credentials + linkage rows out of Postgres. The
    builder picks proxy-via-Panorama if both `Device.proxy_via_panorama`
    and `Panorama.proxy_upgrades` are True, falling back to direct.

    Historical bug: this function was named `precheck_svc.build_client`
    in the upgrader codebase. Phase 4b lifted the builder out into
    `core.command_proxy.builder.build_client_with_fallback` but
    `_client_for` was never updated — it kept calling
    `precheck_svc.build_client(device)`, which had been deleted. The
    error never surfaced because every upgrade attempt we'd run
    crashed at precheck (locale bug, candidate_config FAIL, etc.)
    before reaching the first `_phase_snapshot` call that uses
    `_client_for`. Job #2 was the first one to clear precheck.
    """
    from app.core.command_proxy.builder import build_client_with_fallback  # noqa: PLC0415

    client, _route = build_client_with_fallback(db, device)
    return client


def _upgrade_route_reachable(db: Session, device: Device) -> bool:
    """One-shot: is the device answering on its upgrade route right now?

    Builds the proxy-by-default client and runs a light, auth-proving op.
    `build_client_with_fallback` already probes on the proxy path (and
    raises if neither proxy nor direct works), but the direct path is
    returned WITHOUT a probe — so the explicit get_system_info() is what
    proves reachability there. Swallows everything and returns a bool;
    callers poll on it.
    """
    try:
        client = _client_for(db, device)
        client.get_system_info()
        return True
    except Exception as exc:  # noqa: BLE001
        log.info("Upgrade route to %s not yet reachable: %s", device.name, exc)
        return False


def _wait_for_upgrade_route_ready(
    db: Session,
    device: Device,
    *,
    timeout_s: int = POSTCHECK_ROUTE_TIMEOUT_S,
    poll_interval_s: int = POSTCHECK_ROUTE_POLL_S,
) -> bool:
    """Poll `_upgrade_route_reachable` until True or `timeout_s` elapses.

    Always makes at least one attempt (so a zero/negative timeout still
    probes once). Returns True as soon as the route answers; False on
    timeout — the caller proceeds anyway and lets the postcheck record the
    unreachable error as before, so this only ever helps, never blocks.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        if _upgrade_route_reachable(db, device):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_interval_s)


def _is_already_at_target(device: Device, target_version: str) -> bool:
    """Already running the target version? Skip the install dance for this device.

    Exact-match the version string the operator picked. We get the device's
    current_version fresh from the precheck phase's probe — that's why this
    check is meaningful (and why it has to happen AFTER precheck).
    """
    current = (device.current_version or "").strip()
    return current != "" and current == (target_version or "").strip()


def _verify_install_applied(
    db: Session, job: UpgradeJob, task: DeviceUpgradeTask, device: Device
) -> bool:
    """Confirm the install ACTUALLY took: after install + reboot, the device
    must be RUNNING the target version.

    Why this exists (issue #186): an install job that "did not FIN cleanly"
    (most often the device is out of disk and the image can't be written) can
    reboot the device straight back onto the OLD image. Without this check we'd
    mark the task `install_complete` and report a non-upgrade as a successful
    upgrade — exactly what happened to branch1fw01 / Branch 3 / HG440 in job 12.

    The caller runs the post-reboot probe just before this, so `current_version`
    is fresh. On mismatch we CLEAR `software_installed` so a Retry genuinely
    re-issues the install (otherwise the stale marker skips it and the device
    can never converge), then fail the task. Returns True when on target, False
    (task failed) otherwise.
    """
    db.refresh(device)
    if _is_already_at_target(device, job.target_version):
        return True
    _unmark_phase(db, task, "software_installed")
    _record(
        db, task,
        f"Install did not apply — device is still on "
        f"{device.current_version or 'an unknown version'} after reboot "
        f"(target {job.target_version}). The most common cause is insufficient "
        f"disk for the install; free disk space and retry.",
        phase=TaskPhase.FAILED,
    )
    _fail_job(db, job.id, f"Install did not apply for {device.name} (still on old version)")
    return False


def _is_major_jump(current_version: str | None, target_version: str) -> bool:
    """True when the target crosses the X.Y feature-train boundary vs. the
    device's current version — the only case that needs the target train's base
    image pre-downloaded (11.2.x -> 12.1.x: yes; 11.2.4 -> 11.2.11: no).

    If the current version is unknown we return True and let the base-ensure
    step run — it's a no-op when the base is already present, so erring toward
    "check" is safe and cheap.
    """
    tgt = feature_train(target_version)
    if tgt is None:
        return False
    cur = feature_train(current_version)
    if cur is None:
        return True
    return cur != tgt


# Per-task completion markers stored on task.progress.completed_phases.
# Each phase checks for its marker at the top and short-circuits if present;
# on success it appends its marker. This is what makes "Retry job" actually
# resume from the last incomplete step rather than re-running everything.
def _phase_already_done(task: DeviceUpgradeTask, marker: str) -> bool:
    completed = (task.progress or {}).get("completed_phases", [])
    return marker in completed


def _mark_phase_done(db: Session, task: DeviceUpgradeTask, marker: str) -> None:
    """Persist a completion marker so Retry can resume past this phase.

    Uses the dict-copy + reassign pattern: `dict(task.progress or {})`
    creates a fresh plain dict, mutations happen locally, then we set
    `task.progress = progress` to a NEW reference so SQLAlchemy's
    change detection sees a dirty attribute on the next commit.

    Why the copy: the orchestrator commits between mutations in
    places like `_phase_post_snapshot_and_diff` (snapshot_svc.compare
    commits internally). A previous attempt at
    `MutableDict.as_mutable(JSON)` on the column blew up there —
    MutableDict's `flag_modified` raised `InvalidRequestError: not
    present in the object state` against the expired-by-commit
    attribute. Plain JSON + dict copy sidesteps the whole class of bug.
    """
    progress = dict(task.progress or {})
    completed = list(progress.get("completed_phases", []))
    if marker not in completed:
        completed.append(marker)
        progress["completed_phases"] = completed
        task.progress = progress
        db.commit()


def _unmark_phase(db: Session, task: DeviceUpgradeTask, marker: str) -> None:
    """Remove a phase-completion marker. Used by state reconciliation when
    we observe the device hasn't actually completed the phase the marker
    claims. Idempotent — no-op if the marker wasn't set."""
    progress = dict(task.progress or {})
    completed = list(progress.get("completed_phases", []))
    if marker in completed:
        completed.remove(marker)
        progress["completed_phases"] = completed
        task.progress = progress
        db.commit()


def _clear_install_reboot_handles(db: Session, task: DeviceUpgradeTask) -> None:
    """Drop ALL non-blocking install+reboot scratch state so a reconcile-driven
    retry re-issues cleanly instead of polling a dead PAN-OS job id or skipping
    the restart on a stale marker. Covers the issue-once markers
    (``install_issued`` / ``reboot_issued``) and the persisted handles/counters
    (``install_job_id`` / ``install_attempt`` / ``reboot_ok_streak``) plus the
    matching ``_await_or_park`` start clocks. Idempotent."""
    for marker in ("install_issued", "reboot_issued"):
        _unmark_phase(db, task, marker)
    _progress_set(
        db, task,
        install_job_id=None,
        install_attempt=None,
        reboot_ok_streak=None,
    )
    _progress_put_many(
        db, task,
        {
            "_wait_start::install_job": None,
            "_wait_start::reboot_ready": None,
        },
    )


def reconcile_markers_with_device_state(
    db: Session, job: UpgradeJob, task: DeviceUpgradeTask, device: Device
) -> list[str]:
    """Fix lying phase markers BEFORE walking the orchestrator's phases.

    Phase markers persist completion across retries — that's how Retry-job
    resumes from the last incomplete step. But markers can lie:

      - A user manually finished an install outside the app (PAN-OS UI,
        another orchestrator, even a tech doing the wrong thing) and now
        the device IS at target, but install_complete isn't marked.
        Without reconciliation, retry would re-install and re-reboot — a
        big waste of a maintenance window.

      - resume_ha returned HTTP 200 and we marked ha_resume_complete on
        success, but the firewall silently ignored it and is still
        suspended. Without reconciliation, retry would skip the resume,
        the device stays suspended forever, and operators have to
        manually intervene.

      - An aborted previous job left install_complete marked but the
        device crashed mid-reboot and never installed. (Unusual but
        we've seen it.)

    This function reads the device's CURRENT runtime state and either
    sets or clears each marker so the orchestrator's marker-driven flow
    matches reality. Returns the list of marker corrections applied
    (for the timeline + tests).
    """
    corrections: list[str] = []

    # We need fresh state — what the device says NOW, not what we cached
    # before. Re-probing is cheap and we're about to do a long upgrade
    # anyway.
    try:
        probed = False
        # Local import to keep the top-of-file import list focused.
        from app.upgrade.services import precheck as precheck_svc  # noqa: PLC0415
        precheck_svc.probe_device(db, device, client=_client_for(db, device))
        db.refresh(device)
        probed = True
    except Exception as exc:  # noqa: BLE001
        log.info(
            "Reconciliation probe of %s failed: %s — proceeding with cached state",
            device.name, exc,
        )
        probed = False

    target_at = _is_already_at_target(device, job.target_version)
    ha_state = (device.ha_state or "").lower().strip()
    ha_healthy = _is_ha_healthy(device)

    # ---- install_complete ----
    # If device is at target version, install obviously completed; mark it
    # so we don't redo install+reboot.
    if target_at and not _phase_already_done(task, "install_complete"):
        _mark_phase_done(db, task, "install_complete")
        # Also mark ensure_image — install can only have completed if the
        # image was there.
        if not _phase_already_done(task, "ensure_image"):
            _mark_phase_done(db, task, "ensure_image")
        # Any in-flight non-blocking install/reboot handles are moot now that the
        # device is confirmed at target — drop them so they can't mislead a later
        # entry into polling a stale PAN-OS job.
        _clear_install_reboot_handles(db, task)
        corrections.append("install_complete (device is at target version)")
    # Inverse: marker says install done but device is on old version → lie.
    # Only trust the probe (don't clear if we couldn't get fresh state).
    elif probed and not target_at and _phase_already_done(task, "install_complete"):
        _unmark_phase(db, task, "install_complete")
        # "software_installed" gates the install step inside the same block; if
        # the device isn't actually at target, the install didn't stick either —
        # clear it so a retry re-installs rather than only re-issuing the reboot.
        _unmark_phase(db, task, "software_installed")
        # The non-blocking install/reboot are issue-once behind their own markers
        # + persisted PAN-OS job ids; clear ALL of that so a retry genuinely
        # re-issues the install and reboot rather than polling a dead job id or
        # skipping the restart on a stale reboot_issued marker.
        _clear_install_reboot_handles(db, task)
        corrections.append(
            f"-install_complete (marker was set but device is on "
            f"{device.current_version}, not {job.target_version})"
        )

    # ---- ha_resume_complete ----
    # Only meaningful for paired devices. If the device is in a healthy
    # HA state, the resume DID work, even if a prior crash kept us from
    # marking it. Conversely, if marker says resumed but state is
    # suspended/initial/non-functional, the resume didn't take.
    if device.ha_peer_id is not None:
        if ha_healthy and not _phase_already_done(task, "ha_resume_complete"):
            _mark_phase_done(db, task, "ha_resume_complete")
            corrections.append(f"ha_resume_complete (HA state is '{ha_state}')")
        elif (
            probed
            and not ha_healthy
            and ha_state  # don't clear on an empty/unknown read
            and _phase_already_done(task, "ha_resume_complete")
        ):
            _unmark_phase(db, task, "ha_resume_complete")
            # The inner issue-once guard for the resume itself — clear it so a
            # retry actually re-runs the subsystem-ready wait + resume + verify
            # rather than skipping straight to re-polling the (still-bad) state.
            _unmark_phase(db, task, "ha_resumed")
            corrections.append(
                f"-ha_resume_complete (marker was set but HA state is '{ha_state}')"
            )

    # ---- suspend_ha ----
    # If the device is currently suspended, suspend_ha effectively
    # happened — mark it so retry doesn't issue a redundant suspend.
    # The reverse (clearing the marker when state is healthy) would
    # cause the next retry to RE-SUSPEND a healthy device — bad. Don't
    # do it.
    if ha_state == "suspended" and not _phase_already_done(task, "suspend_ha"):
        _mark_phase_done(db, task, "suspend_ha")
        corrections.append("suspend_ha (device is currently suspended)")

    if corrections:
        _record(
            db, task,
            "State reconciliation: " + "; ".join(corrections),
        )
    return corrections


def _is_ha_healthy(device: Device) -> bool:
    """True when the device's HA state is a normal operating state — i.e.
    we don't need to do recovery work like HA resume."""
    state = (device.ha_state or "").lower().strip()
    return state in {"active", "passive", "active-primary", "active-secondary"}


def _failing_check_names(results: dict) -> list[str]:
    """From a PrecheckRun.results dict, return the names of FAIL-severity checks."""
    out: list[str] = []
    for name, r in (results or {}).items():
        if isinstance(r, dict) and r.get("severity") == "fail":
            out.append(name)
    return sorted(out)


def _wait_for_download_job(
    client: PanDeviceClient,
    job_id: str,
    *,
    task: DeviceUpgradeTask | None = None,
    db: Session | None = None,
) -> bool:
    # SUPERSEDED by the non-blocking poll inside `_download_step` (issue #185);
    # retained only as a dependency of the test-retained `_download_image_with_retry`.
    deadline = time.monotonic() + INSTALL_JOB_TIMEOUT_S  # generous; reused for downloads too
    while time.monotonic() < deadline:
        time.sleep(INSTALL_JOB_POLL_S)
        try:
            status = client.get_job_status(job_id)
        except Exception:  # noqa: BLE001
            continue
        if task is not None and db is not None:
            _set_job_progress(db, task, "download_progress", status.get("progress"))
        if status.get("status") == "FIN":
            return True  # caller verifies via software list
    return False


def _wait_for_ha_subsystem_ready(
    db: Session, task: DeviceUpgradeTask, device: Device
) -> bool:
    """After wait_for_ready (mgmt API back), poll until the HA subsystem is
    actually ready to accept control commands.

    Symptom we're guarding against: get_system_info returns 200 but the HA
    daemon is still in 'initial' / not-yet-talking state. resume_ha then
    blows up with Connection refused because the HA management socket
    isn't listening yet. Acceptable end states are anything other than
    'initial' / unknown — once the daemon is talking, retries handle the
    rest.

    Rebuilds the client each iteration because the upstream socket may
    have been torn down during the reboot.
    """
    deadline = time.monotonic() + HA_SUBSYS_TIMEOUT_S
    last_state = None
    while time.monotonic() < deadline:
        try:
            client = _client_for(db, device)
            info = client.get_system_info()
            state = (info.ha_state or "").lower().strip()
            if state and state != "initial":
                if state != last_state:
                    _record(db, task, f"HA subsystem ready (state: '{state}')")
                return True
            if state != last_state:
                _record(db, task, f"HA subsystem still '{state or 'unknown'}', waiting…")
                last_state = state
        except Exception as exc:  # noqa: BLE001
            # Common during this window — log and keep polling.
            _record(db, task, f"HA subsystem probe failed ({exc}); retrying")
        time.sleep(HA_SUBSYS_POLL_S)
    return False


def _ha_op_with_retry(
    db: Session,
    task: DeviceUpgradeTask,
    device: Device,
    op_name: str,
    op_fn,
) -> bool:
    """Wrap an HA control op (suspend / resume / trigger_failover) in a
    retry loop. The PAN-OS HA daemon flaps briefly during boot/install
    transitions — one Connection-refused doesn't mean the device is
    actually broken. Returns True on success, False on permanent failure.
    """
    last_exc: Exception | None = None
    for attempt in range(1, HA_OP_RETRIES + 1):
        try:
            client = _client_for(db, device)  # fresh socket each attempt
            op_fn(client)
            if attempt > 1:
                _record(db, task, f"{op_name} succeeded on attempt {attempt}")
            return True
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < HA_OP_RETRIES:
                _record(
                    db, task,
                    f"{op_name} attempt {attempt}/{HA_OP_RETRIES} failed: {exc}. "
                    f"Retrying in {HA_OP_RETRY_SLEEP_S}s",
                )
                time.sleep(HA_OP_RETRY_SLEEP_S)
            else:
                _record(
                    db, task,
                    f"{op_name} failed after {HA_OP_RETRIES} attempts; last error: {exc}",
                )
    log.warning("%s exhausted retries: %s", op_name, last_exc)
    return False


def _verify_resume_took_effect(
    db: Session, task: DeviceUpgradeTask, device: Device
) -> bool:
    """Confirm resume_ha actually moved the device off 'suspended'.

    PAN-OS commonly returns HTTP 200 on `request high-availability state
    functional` even when the HA daemon ignores the command — for
    example, when the peer is on a different content version, when the
    HA hold-down timer hasn't elapsed, or when the peer is still
    rebooting. Without this check, we'd skip straight to
    wait_for_passive and burn the full 10-minute timeout watching
    'suspended' before failing.

    Strategy: poll the state for RESUME_VERIFY_WAIT_S seconds. If it
    moves off 'suspended' (to anything — initial / non-functional /
    passive / active) we declare the resume took effect; downstream
    wait_for_passive owns the "is the new state acceptable" question.
    If it stays 'suspended' across the whole window, re-issue
    resume_ha and try again, up to RESUME_VERIFY_REISSUES times.

    Returns True when the state has moved off suspended; False after
    all re-issues are exhausted.
    """
    for attempt in range(RESUME_VERIFY_REISSUES + 1):
        deadline = time.monotonic() + RESUME_VERIFY_WAIT_S
        last_seen = "unknown"
        while time.monotonic() < deadline:
            try:
                info = _client_for(db, device).get_system_info()
                state = (info.ha_state or "").lower().strip()
                last_seen = state or "unknown"
                if state and state != "suspended":
                    _record(
                        db, task,
                        f"Resume verified — HA state moved to '{state}'",
                    )
                    return True
            except Exception as exc:  # noqa: BLE001
                log.warning("Resume-verify poll for %s failed: %s", device.name, exc)
            time.sleep(5)

        # Still suspended at the end of the window. Re-issue resume_ha and
        # try once more — unless we've exhausted our attempts.
        if attempt < RESUME_VERIFY_REISSUES:
            _record(
                db, task,
                f"Device still 'suspended' {RESUME_VERIFY_WAIT_S}s after "
                f"resume (attempt {attempt + 1}/{RESUME_VERIFY_REISSUES + 1}); "
                f"re-issuing resume_ha",
            )
            if not _ha_op_with_retry(
                db, task, device, "HA resume (re-issue)", lambda c: c.resume_ha()
            ):
                # resume_ha itself is now failing — don't keep retrying
                # silently; let the caller's failure path take over.
                _record(
                    db, task,
                    "HA resume re-issue failed at the API layer; aborting verify",
                )
                return False
    _record(
        db, task,
        f"After {RESUME_VERIFY_REISSUES + 1} resume attempts, device is "
        f"still in '{last_seen}' state",
    )
    return False


def _wait_for_passive(
    client: PanDeviceClient, db: Session, task: DeviceUpgradeTask, device: Device
) -> bool:
    """Poll the device's HA state until it's 'passive' (or another acceptable
    rejoined state) or we time out. Updates device.ha_state on each poll so
    the UI reflects progress. Returns True on success.

    SUPERSEDED in the live flow by the NON-BLOCKING `_passive_step` (issue #185):
    the HA-resume block no longer calls this. Retained for now as a reference for
    the diagnostics shape (_passive_step stashes the identical block) and any
    out-of-tree callers; no in-tree caller remains.

    Acceptable end states: 'passive', 'active-secondary'. A pair can converge
    in either depending on its config, but BOTH mean the device has rejoined
    and the other peer is safe to suspend for failover.

    On timeout we record the LAST observed state + the count of polls onto
    task.progress so the failure record explains what actually happened
    (instead of just "timed out"). Common timeout-state patterns:
      - 'initial'      : HA daemon not finished negotiating with peer.
                         Often a heartbeat / preempt setting issue.
      - 'non-functional': dataplane fault or config-sync mismatch.
                         Look at `show high-availability state` on the
                         device for the specific reason.
      - 'suspended'    : resume_ha didn't take. Could be HA hold time
                         not elapsed, or the peer rejected the rejoin.
      - 'active'       : the upgraded device became active instead of
                         passive. Happens when preempt fires and the
                         active peer is "lower priority." Not a disaster
                         — failover would have done this anyway — but our
                         flow expects 'passive' first, so we treat it as
                         a failure that the operator should ack.
    """
    safe_states = {"passive", "active-secondary"}
    deadline = time.monotonic() + HA_PASSIVE_TIMEOUT_S
    last_state: str | None = None
    polls = 0
    states_seen: list[str] = []
    while time.monotonic() < deadline:
        try:
            info = client.get_system_info()
        except Exception as exc:  # noqa: BLE001
            log.warning("HA-state poll for %s failed: %s", device.name, exc)
            time.sleep(HA_PASSIVE_POLL_S)
            polls += 1
            continue
        state = (info.ha_state or "").lower().strip()
        polls += 1
        if state != last_state:
            _record(db, task, f"HA state observed: '{state or 'unknown'}'")
            last_state = state
            states_seen.append(state or "unknown")
            # Also keep the device row fresh so other watchers see it.
            device.ha_state = info.ha_state
            db.commit()
        if state in safe_states:
            return True
        time.sleep(HA_PASSIVE_POLL_S)

    # Stash the diagnostic context on task.progress so the failure handler
    # (and the UI) can render a much more useful error than just "timed out."
    progress = dict(task.progress or {})
    progress["wait_for_passive_diagnostics"] = {
        "last_state": last_state or "unknown",
        "states_seen": states_seen,
        "polls": polls,
        "timeout_s": HA_PASSIVE_TIMEOUT_S,
    }
    task.progress = progress
    db.commit()
    return False


def _format_passive_timeout_message(
    db: Session, task: DeviceUpgradeTask, device: Device
) -> str:
    """Build the failure-record line for a wait_for_passive timeout.

    Three things the operator wants to know immediately:
      1. What state DID the device land in? (read from the diagnostic
         block stashed by _wait_for_passive)
      2. What state is the OTHER member of the pair in right now? — this
         determines whether the cluster is degraded but serving traffic
         (other peer is 'active') or fully down (other peer is also
         abnormal).
      3. Why is the other peer at the OLD version? Because the orchestrator
         intentionally upgrades passive → failover → active, and a passive
         failure halts the whole flow BEFORE we touch the active. That's
         a safety feature, not a bug — but it's not obvious until you've
         read the code.

    Worst case (peer query fails, diagnostics missing) we fall back to the
    old generic message so the failure isn't lost.
    """
    diag = (task.progress or {}).get("wait_for_passive_diagnostics", {})
    last_state = diag.get("last_state") or "unknown"
    states_seen = diag.get("states_seen") or []
    timeout_s = diag.get("timeout_s", HA_PASSIVE_TIMEOUT_S)

    # Probe the peer fresh — we want CURRENT state, not the cached row,
    # because the peer may have changed during the long wait window.
    peer_summary = "peer status unavailable"
    if device.ha_peer_id is not None:
        peer = db.get(Device, device.ha_peer_id)
        if peer is not None:
            try:
                peer_client = _client_for(db, peer)
                peer_info = peer_client.get_system_info()
                peer.ha_state = peer_info.ha_state
                peer.current_version = peer_info.sw_version or peer.current_version
                db.commit()
                peer_summary = (
                    f"peer {peer.name} is currently "
                    f"'{peer_info.ha_state or 'unknown'}' on "
                    f"{peer_info.sw_version or 'unknown'}"
                )
            except Exception as exc:  # noqa: BLE001
                peer_summary = (
                    f"peer {peer.name} could not be probed ({exc}); "
                    f"last-known state '{peer.ha_state or 'unknown'}' on "
                    f"{peer.current_version or 'unknown'}"
                )

    hint = _passive_state_hint(last_state)
    states_trail = " → ".join(states_seen) if states_seen else "(none observed)"

    return (
        f"Timed out after {timeout_s}s waiting for {device.name} to reach "
        f"'passive' state. Last HA state observed: '{last_state}'. "
        f"State progression during wait: {states_trail}. "
        f"{peer_summary}. "
        f"{hint} "
        "The other pair member was intentionally NOT installed — this "
        "orchestrator upgrades passive → failover → active in sequence, "
        "so a passive failure halts the flow before the active is touched "
        "(safety: never break both members at once). Use 'Retry job' to "
        "resume from this step once the HA issue is resolved on the device."
    )


def _passive_state_hint(state: str) -> str:
    """One-liner action hint for the most common stuck-states."""
    state = (state or "").lower().strip() or "unknown"
    hints = {
        "initial": (
            "'initial' means the HA daemon is still negotiating with the peer. "
            "Check `show high-availability state` on both devices for the "
            "reason — often a heartbeat link issue or version-mismatch hold."
        ),
        "non-functional": (
            "'non-functional' indicates a dataplane fault or HA-config / "
            "content-version mismatch with the peer. `show high-availability "
            "state` will name the specific reason."
        ),
        "suspended": (
            "'suspended' means the resume command didn't take effect. The HA "
            "hold-down timer may not have elapsed, or the peer may have "
            "rejected the rejoin. Try `request high-availability state "
            "functional` on the device manually after resolving the cause."
        ),
        "active": (
            "'active' instead of 'passive' usually means preempt fired and "
            "the upgraded device took over. Functionally fine, but our flow "
            "expects 'passive' first; you can manually `request high-"
            "availability state suspend/functional` to swap roles, then retry."
        ),
        "unknown": (
            "No HA state could be read from the device during the wait. "
            "Verify mgmt-plane connectivity and that HA is configured."
        ),
    }
    return hints.get(state, f"State '{state}' is not a recognized stuck-state — manual inspection recommended.")


def _set_job_progress(db: Session, task: DeviceUpgradeTask, key: str, percent) -> None:
    """Write a percentage onto task.progress without spamming the DB on
    no-op updates (PAN-OS returns the same percent across multiple polls
    while a phase is paused on a sub-step)."""
    if percent is None:
        return
    try:
        new_pct = int(percent)
    except (TypeError, ValueError):
        return
    progress = dict(task.progress or {})
    if progress.get(key) == new_pct:
        return
    progress[key] = new_pct
    task.progress = progress
    db.commit()


def _wait_for_install_job(
    client: PanDeviceClient,
    job_id: str,
    *,
    task: DeviceUpgradeTask | None = None,
    db: Session | None = None,
) -> bool:
    """Poll an install job. Returns True if we observed FIN with result OK,
    False otherwise. While polling, write the percentage onto task.progress
    so the UI can render a progress bar.

    SUPERSEDED by the non-blocking poll inside `_install_step` (issue #185);
    retained only as a dependency of the test-retained `_install_with_retry`.
    """
    deadline = time.monotonic() + INSTALL_JOB_TIMEOUT_S
    consecutive_errors = 0
    while time.monotonic() < deadline:
        time.sleep(INSTALL_JOB_POLL_S)
        try:
            status = client.get_job_status(job_id)
            consecutive_errors = 0
        except Exception:  # noqa: BLE001
            consecutive_errors += 1
            if consecutive_errors >= 2:
                return False
            continue
        # Surface progress to the UI.
        if task is not None and db is not None:
            _set_job_progress(db, task, "install_progress", status.get("progress"))
        if status.get("status") == "FIN":
            return status.get("result") == "OK"
    return False


class _OverrideOutcome(enum.Enum):
    """Three-state classification of an operator's choice at a check-failure
    override gate, returned by `_resolve_override_action`. Callers in the
    phase functions branch on these.

    - PROCEED: operator clicked "Override + proceed" — accept the
      check failures as-is and advance to the next phase.
    - RERUN: operator clicked "Re-run check" (e.g. "I fixed the
      candidate config push; try again"). Phase function should
      loop back and re-execute the check instead of advancing.
    - ABORT: operator aborted the job (or another task failed it).
      Caller propagates failure.
    """

    PROCEED = "proceed"
    RERUN = "rerun"
    ABORT = "abort"


# Token-value sentinels written by the route endpoints. `_resolve_override_action`
# inspects the token to disambiguate proceed vs rerun. Any other non-empty token
# value falls through to PROCEED for backward-compat (the older /override
# endpoint set a random hex value).
_RERUN_TOKEN_PREFIX = "RERUN_"


def _set_phase(db: Session, task: DeviceUpgradeTask, phase: TaskPhase) -> None:
    task.phase = phase
    task.tick_count = (task.tick_count or 0) + 1
    # Clear any sub-step label when the MAJOR phase changes — a stale
    # "rebooting" must not bleed into POSTCHECK. The substep is a
    # within-phase refinement; the phase change is the reset point.
    progress = dict(task.progress or {})
    if progress.get("phase_substep") is not None:
        progress["phase_substep"] = None
        task.progress = progress
    db.commit()
    db.refresh(task)


def _set_substep(db: Session, task: DeviceUpgradeTask, substep: str | None) -> None:
    """Set the within-phase sub-step label shown in the UI as
    "UPGRADE SECONDARY · rebooting".

    The big phases (UPGRADE_*, FAILOVER) span several minutes of distinct
    internal work — install, reboot, mgmt-plane wait, HA resume,
    wait-for-passive — that all share one phase enum value. Without a
    sub-step the badge looks frozen for ~10 min during a reboot. This
    writes `progress.phase_substep`; the frontend renders it next to the
    phase badge and falls back to the raw phase when it's null.

    No schema change — `phase_substep` rides in the existing progress
    JSON. Idempotent on no-op (same value → skip the commit).
    """
    progress = dict(task.progress or {})
    if progress.get("phase_substep") == substep:
        return
    progress["phase_substep"] = substep
    task.progress = progress
    db.commit()


def _record(db: Session, task: DeviceUpgradeTask, message: str, *, phase: TaskPhase | None = None) -> None:
    """Append a timestamped line to task.progress.log and optionally set phase + error."""
    progress = dict(task.progress or {})
    log_lines = progress.get("log") or []
    log_lines.append(f"{_now_iso()} {message}")
    progress["log"] = log_lines
    task.progress = progress
    if phase is not None:
        task.phase = phase
        if phase == TaskPhase.FAILED:
            task.error = message
    db.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _crash_hint(exc: BaseException) -> str:
    """Short, actionable hint appended to driver-crash messages for the common
    'can't reach the device through Panorama' failure — usually a just-rebooted
    device Panorama hasn't re-registered yet, or a firewall now running a newer
    PAN-OS than Panorama (Panorama must be >= its firewalls). Empty otherwise."""
    msg = str(exc).lower()
    if any(s in msg for s in ("panorama unreachable", "no direct route", "timed out", "not connected")):
        return (
            " — couldn't reach the device through Panorama. If it was just "
            "upgraded, Panorama may still be re-registering it post-reboot, or "
            "the firewall is now newer than Panorama (Panorama must be >= the "
            "firewall). Confirm Panorama shows it connected, then Retry to resume."
        )
    return ""


def _driver_failure_message(exc: BaseException) -> str:
    """Operator-facing message for a drive_pair failure.

    A time-limit trip (SoftTimeLimitExceeded) gets a distinct, actionable
    message — the task was killed to free the worker because a device/Panorama
    op was hanging, and Retry resumes from the last completed marker. Every
    other error uses the crash hint.
    """
    if isinstance(exc, SoftTimeLimitExceeded):
        return (
            "Upgrade task hit its time limit and was stopped to free the "
            "upgrade worker — a device or Panorama operation was likely "
            "hanging. Click Retry to resume from the last completed step; if it "
            "keeps timing out, confirm the device and its Panorama are reachable."
        )
    return f"Upgrade driver error: {exc}{_crash_hint(exc)}"


def _warn_if_target_exceeds_panorama(
    db: Session, job: UpgradeJob, task: DeviceUpgradeTask, device: Device
) -> None:
    """Advisory check: warn if the upgrade target is NEWER than the managing
    Panorama. PAN-OS requires Panorama to run >= its firewalls; a firewall ahead
    of Panorama is unsupported, and post-upgrade operations through Panorama
    (how we reach proxied devices) can fail until Panorama is upgraded. This is
    the "someone forgot to upgrade Panorama first" guard — non-blocking; we
    record a clear warning and let the operator proceed."""
    from app.core.command_proxy.builder import panorama_sw_version  # noqa: PLC0415

    pano_ver = panorama_sw_version(device)
    if not pano_ver:
        return
    if version_tuple(job.target_version) > version_tuple(pano_ver):
        pano = device.panorama
        _record(
            db, task,
            f"WARNING: target {job.target_version} is newer than the managing "
            f"Panorama ({pano.name} on {pano_ver}). PAN-OS requires Panorama >= its "
            f"firewalls — upgrading the firewall ahead of Panorama is unsupported, "
            f"and post-upgrade checks through Panorama may fail until Panorama is "
            f"upgraded. Consider upgrading Panorama first.",
        )


def _fail_job(db: Session, job_id: int, reason: str) -> None:
    """Record a DEVICE-level failure reason on the job — WITHOUT marking the
    whole job FAILED.

    Failure isolation: one device failing must not stop the others. The
    per-device failure is already recorded on its task (via
    ``_record(..., phase=FAILED)``); here we only keep the first such reason on
    the job as a useful root-cause hint. The terminal JOB state is decided by
    ``_maybe_mark_job_done`` once every task reaches DONE/FAILED:
        all DONE -> COMPLETED; some of each -> COMPLETED_WITH_ERRORS;
        all FAILED -> FAILED.

    (The name is kept for its many call sites; it no longer fails the job.)
    """
    job = db.get(UpgradeJob, job_id)
    if job is None:
        return
    # First-write-wins: the first device failure is the most useful root cause
    # to surface if the whole job ends up FAILED.
    if reason and not job.failure_reason:
        job.failure_reason = reason
        db.commit()


# Job states that mean "no more orchestrator work will run for this job".
_TERMINAL_JOB_STATES = (
    JobState.COMPLETED,
    JobState.COMPLETED_WITH_ERRORS,
    JobState.FAILED,
    JobState.ABORTED,
)


def _job_terminal(db: Session, job: UpgradeJob) -> bool:
    db.refresh(job)
    return job.state in _TERMINAL_JOB_STATES


def _fail_pair_if_partial(db: Session, tasks: list[DeviceUpgradeTask]) -> None:
    """Fail an HA pair atomically: if any task in this pair/standalone ended
    FAILED, fail the rest of the pair too (you can't half-upgrade an HA pair,
    and a partner left at a non-terminal phase would keep the whole job RUNNING
    forever). DONE tasks are left alone — they genuinely succeeded."""
    if not any(t.phase == TaskPhase.FAILED for t in tasks):
        return
    for t in tasks:
        if t.phase not in (TaskPhase.DONE, TaskPhase.FAILED):
            _record(
                db, t,
                "Pair upgrade stopped because its HA partner failed.",
                phase=TaskPhase.FAILED,
            )


def _maybe_mark_job_done(db: Session, job_id: int) -> None:
    """Compute the terminal job state once EVERY task has reached a terminal
    phase (DONE or FAILED). Until then the job stays RUNNING — a parked or
    in-flight task keeps it open.

    Failure isolation: a single FAILED task no longer fails the whole job.
        all DONE                -> COMPLETED
        mix of DONE and FAILED  -> COMPLETED_WITH_ERRORS
        all FAILED              -> FAILED
    """
    job = db.get(UpgradeJob, job_id)
    if job is None or job.state in _TERMINAL_JOB_STATES:
        return
    tasks = db.query(DeviceUpgradeTask).filter(DeviceUpgradeTask.job_id == job_id).all()
    terminal = (TaskPhase.DONE, TaskPhase.FAILED)
    if not tasks or not all(t.phase in terminal for t in tasks):
        return  # still in flight, or parked at a gate — leave the job RUNNING
    failed = sum(1 for t in tasks if t.phase == TaskPhase.FAILED)
    total = len(tasks)
    job.finished_at = datetime.now(timezone.utc)
    if failed == 0:
        job.state = JobState.COMPLETED
    elif failed == total:
        job.state = JobState.FAILED
        if not job.failure_reason:
            job.failure_reason = f"All {total} device(s) failed."
    else:
        job.state = JobState.COMPLETED_WITH_ERRORS
        job.failure_reason = (
            f"{failed} of {total} device(s) failed; {total - failed} succeeded. "
            "See each device's timeline for details."
        )
    db.commit()
