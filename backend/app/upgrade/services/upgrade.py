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

from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from app.core.command_proxy.pan_client import PanDeviceClient
from app.core.devices.models.device import Device
from app.core.devices.models.enums import HARole
from app.db import SessionLocal
from app.upgrade.models.enums import JobState, Severity, TaskPhase
from app.upgrade.models.job import DeviceUpgradeTask, UpgradeJob
from app.upgrade.models.precheck import PrecheckRun
from app.upgrade.models.precheck_set import PrecheckSet
from app.upgrade.models.snapshot import Snapshot, SnapshotKind
from app.upgrade.services import precheck as precheck_svc
from app.upgrade.services import snapshot as snapshot_svc
from app.upgrade.services.precheck_classifier import classify, overall_severity

log = logging.getLogger(__name__)

# How long to wait for a device to come back from a reboot.
REBOOT_TIMEOUT_S = 30 * 60
REBOOT_POLL_S = 30

# How long to wait for an install JOB to FIN before assuming it's wedged.
INSTALL_JOB_TIMEOUT_S = 30 * 60
INSTALL_JOB_POLL_S = 20

# How long to park at a confirmation gate before giving up.
CONFIRM_TIMEOUT_S = 24 * 60 * 60
CONFIRM_POLL_S = 5

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

        # When the last pair finishes successfully, flip the job to COMPLETED.
        _maybe_mark_job_done(db, job_id)
    except Exception:
        log.exception("drive_pair crashed for job=%s key=%s", job_id, ha_pair_key)
        _fail_job(db, job_id, "driver crashed; see worker logs")
    finally:
        db.close()


# ---------- standalone flow ----------


def _drive_solo(db: Session, job: UpgradeJob, task: DeviceUpgradeTask) -> None:
    device = task.device

    if not _phase_precheck(db, job, task, device):
        return
    if not _phase_snapshot(db, job, task, device):
        return
    if not _phase_ensure_image(db, job, task, device):
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
    passive, active = _classify_pair(tasks)
    if passive is None or active is None:
        # If HA roles are unclear (both UNKNOWN, both ACTIVE, etc.) we refuse.
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

    # ---- Phase: upgrade the passive ----
    if not _phase_suspend_ha(db, job, passive, passive.device):
        return
    _set_phase(db, passive, TaskPhase.UPGRADE_SECONDARY)
    if not _phase_install_and_wait(db, job, passive, passive.device, started_phase=TaskPhase.UPGRADE_SECONDARY):
        return
    _set_phase(db, passive, TaskPhase.POSTCHECK_SECONDARY)
    if not _phase_postcheck(db, job, passive, passive.device, finishing_phase=TaskPhase.POSTCHECK_SECONDARY):
        return
    _phase_post_snapshot_and_diff(db, job, passive, passive.device)

    # ---- Phase: failover ----
    if job.require_failover_confirmation:
        if not _wait_for_confirm(db, passive, TaskPhase.AWAITING_FAILOVER_CONFIRM):
            return
    _set_phase(db, passive, TaskPhase.FAILOVER)
    if not _phase_failover(db, job, active, passive):
        return

    # ---- Optional gate before upgrading the (former) active ----
    if job.require_primary_upgrade_confirmation:
        if not _wait_for_confirm(db, active, TaskPhase.AWAITING_PRIMARY_UPGRADE_CONFIRM):
            return

    # ---- Phase: upgrade the (former) active, now passive ----
    if not _phase_suspend_ha(db, job, active, active.device):
        return
    _set_phase(db, active, TaskPhase.UPGRADE_PRIMARY)
    if not _phase_install_and_wait(db, job, active, active.device, started_phase=TaskPhase.UPGRADE_PRIMARY):
        return
    _set_phase(db, active, TaskPhase.POSTCHECK_PRIMARY)
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
    tasks: list[DeviceUpgradeTask],
) -> tuple[DeviceUpgradeTask | None, DeviceUpgradeTask | None]:
    passive = next((t for t in tasks if t.device.ha_role == HARole.PASSIVE), None)
    active = next((t for t in tasks if t.device.ha_role == HARole.ACTIVE), None)
    return passive, active


# ---------- phase functions ----------


def _phase_precheck(db: Session, job: UpgradeJob, task: DeviceUpgradeTask, device: Device) -> bool:
    if _phase_already_done(task, "precheck"):
        _record(db, task, "Pre-check already completed in a prior run; skipping")
        return True

    # Outer loop supports the operator's "Re-run check" action — if
    # they clicked it at the override gate (e.g. after pushing a
    # fixed candidate config from Panorama), we loop back and execute
    # the precheck fresh instead of advancing past it.
    while True:
        _set_phase(db, task, TaskPhase.PRECHECK)
        try:
            run = precheck_svc.run_precheck_for_device(
                db,
                device,
                checks=_resolve_checks_for_job(db, job),
                user_id=job.created_by_id,
            )
        except Exception as exc:  # noqa: BLE001
            _record(db, task, f"Pre-check raised: {exc}", phase=TaskPhase.FAILED)
            _fail_job(db, job.id, f"Pre-check failed for {device.name}")
            return False

        progress = task.progress or {}
        progress["precheck_run_id"] = run.id
        progress["precheck_overall"] = run.overall_severity.value
        task.progress = progress
        db.commit()

        if run.overall_severity != Severity.FAIL:
            break  # pass / warn / skip → advance

        # FAIL — park at override gate so operator can read what
        # failed, decide if it's acceptable, click Proceed anyway,
        # click Re-run check, or Abort the job.
        failing = _failing_check_names(run.results)
        msg = (
            f"Pre-check FAILED: {', '.join(failing) if failing else 'see precheck run for details'}."
            f" Job parked — click Override + proceed, Re-run check, or Abort job."
        )
        progress["failing_checks"] = failing
        task.progress = progress
        db.commit()
        _record(db, task, msg)
        if job.auto_ack_precheck_failures:
            # Pre-authorized by the operator at job creation. Log the
            # bypass explicitly so the timeline shows WHY the job didn't
            # park — a future audit needs to see the override happened.
            _record(
                db, task,
                "Pre-check failures auto-acknowledged (auto_ack_precheck_failures "
                "was enabled on this job); proceeding without operator confirmation",
            )
            break
        outcome = _wait_for_override(db, task, TaskPhase.AWAITING_PRECHECK_OVERRIDE)
        if outcome == _OverrideOutcome.ABORT:
            return False
        if outcome == _OverrideOutcome.PROCEED:
            break
        # RERUN — clear the failing_checks list and loop back to the
        # top to execute precheck again. The new run replaces the old.
        progress.pop("failing_checks", None)
        task.progress = progress
        db.commit()
        # Loop continues, _set_phase(PRECHECK) on next iteration.

    _mark_phase_done(db, task, "precheck")
    return True


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
        _client_for(device),
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

    progress = task.progress or {}
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
        _client_for(device),
        SnapshotKind.POST_UPGRADE,
        task_id=task.id,
    )
    if post.error:
        _record(db, task, f"Post-upgrade snapshot failed (non-fatal): {post.error}")
    else:
        _record(db, task, f"Post-upgrade snapshot captured")

    progress = task.progress or {}
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
        client = _client_for(device)
        if client.is_version_downloaded(job.target_version):
            _mark_phase_done(db, task, "ensure_image")
            return True
    except Exception as exc:  # noqa: BLE001
        _record(db, task, f"Couldn't query software list: {exc}", phase=TaskPhase.FAILED)
        _fail_job(db, job.id, f"Image presence check failed for {device.name}")
        return False

    # Inline download; same approach as the stage service but we don't create
    # a separate DeviceStageRun here (the Job timeline carries the visibility).
    try:
        job_id = client.request_software_download(job.target_version)
        if not job_id:
            raise RuntimeError("download request returned no job id")
        if not _wait_for_download_job(client, job_id, task=task, db=db):
            raise RuntimeError("download job did not finish OK")
        if not client.is_version_downloaded(job.target_version):
            raise RuntimeError("post-job software list does not show version downloaded")
    except Exception as exc:  # noqa: BLE001
        _record(db, task, f"Image download failed: {exc}", phase=TaskPhase.FAILED)
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
        # Step 1+2: install.
        try:
            client = _client_for(device)
            install_job = client.request_software_install(job.target_version)
            if not install_job:
                raise RuntimeError("install request returned no job id")
            ok = _wait_for_install_job(client, install_job, task=task, db=db)
            if not ok:
                log.info("Install job %s did not FIN cleanly; proceeding to restart anyway", install_job)
            _record(db, task, "Install complete")
        except Exception as exc:  # noqa: BLE001
            _record(db, task, f"Install failed: {exc}", phase=TaskPhase.FAILED)
            _fail_job(db, job.id, f"Install failed for {device.name}")
            return False

        # Step 3: optional pause before reboot. Default behavior — the
        # operator explicitly clicks Reboot now.
        if not job.auto_reboot_after_install:
            _record(db, task, "Install done; awaiting operator confirmation to reboot")
            if not _wait_for_confirm(db, task, TaskPhase.AWAITING_REBOOT_CONFIRM):
                return False
            _set_phase(db, task, started_phase)

        # Step 4+5: restart and wait for mgmt plane.
        try:
            client = _client_for(device)
            _record(db, task, "Issuing system restart")
            client.restart_system()
            _record(
                db, task,
                f"Waiting for device to come back online "
                f"(up to {REBOOT_TIMEOUT_S}s, with consecutive-success confirmation)",
            )
            client.wait_for_ready(timeout_s=REBOOT_TIMEOUT_S, poll_interval_s=REBOOT_POLL_S)
            _record(db, task, "Device mgmt plane is stable")
        except Exception as exc:  # noqa: BLE001
            _record(db, task, f"Reboot/wait failed: {exc}", phase=TaskPhase.FAILED)
            _fail_job(db, job.id, f"Reboot/wait failed for {device.name}")
            return False

        try:
            precheck_svc.probe_device(db, device, client=_client_for(device))
            db.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("Post-reboot probe failed for %s: %s", device.name, exc)

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

        _record(db, task, "Waiting for HA state to settle to 'passive'")
        if not _wait_for_passive(_client_for(device), db, task, device):
            _record(
                db, task,
                _format_passive_timeout_message(db, task, device),
                phase=TaskPhase.FAILED,
            )
            _fail_job(db, job.id, f"HA never reached 'passive' for {device.name}")
            return False
        _record(db, task, "HA state confirmed 'passive'")
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

    # Same rerun-loop pattern as _phase_precheck — operator can fix a
    # post-upgrade issue and re-run the check rather than override or
    # abort.
    while True:
        _set_phase(db, task, finishing_phase)
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

        progress = task.progress or {}
        progress["postcheck_run_id"] = run.id
        progress["postcheck_overall"] = run.overall_severity.value
        task.progress = progress
        db.commit()

        if run.overall_severity != Severity.FAIL:
            break

        failing = _failing_check_names(run.results)
        msg = (
            f"Post-check FAILED: {', '.join(failing) if failing else 'see post-check run for details'}."
            f" Job parked — click Override + proceed, Re-run check, or Abort job."
        )
        progress["failing_postchecks"] = failing
        task.progress = progress
        db.commit()
        _record(db, task, msg)
        if job.auto_ack_postcheck_failures:
            _record(
                db, task,
                "Post-check failures auto-acknowledged (auto_ack_postcheck_failures "
                "was enabled on this job); proceeding without operator confirmation",
            )
            break
        outcome = _wait_for_override(db, task, TaskPhase.AWAITING_POSTCHECK_OVERRIDE)
        if outcome == _OverrideOutcome.ABORT:
            return False
        if outcome == _OverrideOutcome.PROCEED:
            break
        # RERUN — clear and loop.
        progress.pop("failing_postchecks", None)
        task.progress = progress
        db.commit()

    _mark_phase_done(db, task, "postcheck")
    return True


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
        precheck_svc.probe_device(db, passive_task.device, client=_client_for(passive_task.device))
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


def _client_for(device: Device) -> PanDeviceClient:
    return precheck_svc.build_client(device)


def _is_already_at_target(device: Device, target_version: str) -> bool:
    """Already running the target version? Skip the install dance for this device.

    Exact-match the version string the operator picked. We get the device's
    current_version fresh from the precheck phase's probe — that's why this
    check is meaningful (and why it has to happen AFTER precheck).
    """
    current = (device.current_version or "").strip()
    return current != "" and current == (target_version or "").strip()


# Per-task completion markers stored on task.progress.completed_phases.
# Each phase checks for its marker at the top and short-circuits if present;
# on success it appends its marker. This is what makes "Retry job" actually
# resume from the last incomplete step rather than re-running everything.
def _phase_already_done(task: DeviceUpgradeTask, marker: str) -> bool:
    completed = (task.progress or {}).get("completed_phases", [])
    return marker in completed


def _mark_phase_done(db: Session, task: DeviceUpgradeTask, marker: str) -> None:
    progress = task.progress or {}
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
    progress = task.progress or {}
    completed = list(progress.get("completed_phases", []))
    if marker in completed:
        completed.remove(marker)
        progress["completed_phases"] = completed
        task.progress = progress
        db.commit()


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
        precheck_svc.probe_device(db, device, client=_client_for(device))
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
        corrections.append("install_complete (device is at target version)")
    # Inverse: marker says install done but device is on old version → lie.
    # Only trust the probe (don't clear if we couldn't get fresh state).
    elif probed and not target_at and _phase_already_done(task, "install_complete"):
        _unmark_phase(db, task, "install_complete")
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
            client = _client_for(device)
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
            client = _client_for(device)  # fresh socket each attempt
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
                info = _client_for(device).get_system_info()
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
    progress = task.progress or {}
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
                peer_client = _client_for(peer)
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
    progress = task.progress or {}
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


def _wait_for_confirm(db: Session, task: DeviceUpgradeTask, parking_phase: TaskPhase) -> bool:
    """Park `task` at `parking_phase` and poll until confirmation_token is set
    by the user (via POST /tasks/{id}/confirm) or until the job is aborted/timed out."""
    _set_phase(db, task, parking_phase)
    deadline = time.monotonic() + CONFIRM_TIMEOUT_S
    while time.monotonic() < deadline:
        time.sleep(CONFIRM_POLL_S)
        db.refresh(task)
        if task.confirmation_token:
            # Clear it so a future gate doesn't reuse this confirmation.
            task.confirmation_token = None
            db.commit()
            return True
        # Also check job state — a user could have aborted.
        job = db.get(UpgradeJob, task.job_id)
        if job is None or job.state in (JobState.ABORTED, JobState.FAILED, JobState.COMPLETED):
            return False
    _record(db, task, f"Confirmation timeout at {parking_phase.value}", phase=TaskPhase.FAILED)
    _fail_job(db, task.job_id, "Confirmation timeout")
    return False


class _OverrideOutcome(enum.Enum):
    """Three-state return for `_wait_for_override`. Callers in the
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


# Token-value sentinels written by the route endpoints. `_wait_for_override`
# inspects the token to disambiguate proceed vs rerun. Any other non-empty
# token value falls through to PROCEED for backward-compat (the older
# /override endpoint set a random hex value).
_RERUN_TOKEN_PREFIX = "RERUN_"


def _wait_for_override(
    db: Session, task: DeviceUpgradeTask, parking_phase: TaskPhase
) -> _OverrideOutcome:
    """Park at a check-failure override gate. Same polling shape as
    _wait_for_confirm. Three-state return per `_OverrideOutcome` so
    the calling phase can decide whether to advance, re-run, or fail.
    """
    _set_phase(db, task, parking_phase)
    deadline = time.monotonic() + CONFIRM_TIMEOUT_S
    while time.monotonic() < deadline:
        time.sleep(CONFIRM_POLL_S)
        db.refresh(task)
        if task.confirmation_token:
            tok = task.confirmation_token
            task.confirmation_token = None
            if tok.startswith(_RERUN_TOKEN_PREFIX):
                _record(
                    db, task,
                    f"Operator requested re-run at {parking_phase.value}",
                )
                db.commit()
                return _OverrideOutcome.RERUN
            _record(
                db, task,
                f"Operator overrode check failure at {parking_phase.value}; proceeding.",
            )
            db.commit()
            return _OverrideOutcome.PROCEED
        job = db.get(UpgradeJob, task.job_id)
        if job is None or job.state in (JobState.ABORTED, JobState.FAILED, JobState.COMPLETED):
            # User aborted (or another task failed the job). Mark this task
            # FAILED so the timeline reflects that we stopped here.
            _record(db, task, "Aborted at check-failure override gate", phase=TaskPhase.FAILED)
            return _OverrideOutcome.ABORT
    _record(db, task, f"Override timeout at {parking_phase.value}", phase=TaskPhase.FAILED)
    _fail_job(db, task.job_id, "Override timeout")
    return _OverrideOutcome.ABORT
    return False


def _set_phase(db: Session, task: DeviceUpgradeTask, phase: TaskPhase) -> None:
    task.phase = phase
    task.tick_count = (task.tick_count or 0) + 1
    db.commit()
    db.refresh(task)


def _record(db: Session, task: DeviceUpgradeTask, message: str, *, phase: TaskPhase | None = None) -> None:
    """Append a timestamped line to task.progress.log and optionally set phase + error."""
    progress = task.progress or {}
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


def _fail_job(db: Session, job_id: int, reason: str) -> None:
    job = db.get(UpgradeJob, job_id)
    if job is None:
        return
    if job.state not in (JobState.COMPLETED, JobState.FAILED, JobState.ABORTED):
        job.state = JobState.FAILED
        job.finished_at = datetime.now(timezone.utc)
        db.commit()


def _job_terminal(db: Session, job: UpgradeJob) -> bool:
    db.refresh(job)
    return job.state in (JobState.COMPLETED, JobState.FAILED, JobState.ABORTED)


def _maybe_mark_job_done(db: Session, job_id: int) -> None:
    """Flip the job to COMPLETED iff every task is in a terminal phase and none failed."""
    job = db.get(UpgradeJob, job_id)
    if job is None or job.state in (JobState.FAILED, JobState.ABORTED, JobState.COMPLETED):
        return
    tasks = db.query(DeviceUpgradeTask).filter(DeviceUpgradeTask.job_id == job_id).all()
    if any(t.phase == TaskPhase.FAILED for t in tasks):
        job.state = JobState.FAILED
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        return
    if all(t.phase == TaskPhase.DONE for t in tasks):
        job.state = JobState.COMPLETED
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
