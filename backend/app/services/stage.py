"""Pre-stage a PAN-OS image to a device — download, don't install.

The device pulls the image from updates.paloaltonetworks.com (or its configured
update server). For Panorama-managed devices we proxy the op via Panorama, so
the device doesn't need direct internet from the upgrader's network.

Flow per device:
  1. If the requested version is already downloaded on the device, mark PASS
     immediately (no work needed).
  2. Otherwise issue `request system software download version X.Y.Z`, poll
     the resulting job until FIN, and verify by re-reading the software list.
  3. On success, set Device.staged_version (= the target).

We do NOT try to predict or pre-fetch any "base image" the device might also
need. PAN-OS's own dependency logic varies by major.minor train and is
changing — newer trains skip the base requirement entirely. If a particular
device rejects a request because it wants something else first, that error
surfaces verbatim in the run's `error` field; the operator sees it and can
react.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.enums import Severity
from app.models.stage import DeviceStageRun
from app.services import precheck as precheck_svc
from app.services.pan_client import PanDeviceClient

log = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT_SECONDS = 30 * 60
POLL_INTERVAL_SECONDS = 15


def stage_device_for_version(
    db: Session,
    device: Device,
    version: str,
    *,
    bulk_run_id: int | None = None,
) -> DeviceStageRun:
    """Stage `version` on `device`. Returns the persisted DeviceStageRun.

    Updates Device.staged_version on success, staged_error on failure.
    """
    run = DeviceStageRun(
        device_id=device.id,
        bulk_run_id=bulk_run_id,
        version=version,
        outcome=Severity.FAIL,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        client = precheck_svc.build_client(device)
    except ValueError as exc:
        return _finalize_run(db, run, device, success=False, error=str(exc))

    # Short-circuit: already downloaded?
    try:
        if client.is_version_downloaded(version):
            return _finalize_run(db, run, device, success=True, error=None)
    except Exception as exc:  # noqa: BLE001
        log.warning("Stage: pre-check is_version_downloaded failed for %s: %s", device.name, exc)

    ok, err = _download_and_poll(client, version)
    if not ok:
        return _finalize_run(db, run, device, success=False, error=err)
    return _finalize_run(db, run, device, success=True, error=None)


def _download_and_poll(client: PanDeviceClient, version: str) -> tuple[bool, str | None]:
    """Issue the download op, poll until FIN, then verify the actual outcome
    by checking the device's software list.

    PAN-OS sometimes returns ``<result>FAIL</result>`` on a software-download
    job even when the image actually downloaded successfully. The software
    list is authoritative, so we re-check it once the job hits FIN before
    declaring success or failure.
    """
    try:
        job_id = client.request_software_download(version)
    except Exception as exc:  # noqa: BLE001
        return False, f"download request failed: {exc}"
    if not job_id:
        return False, "download request returned no job id"

    deadline = time.monotonic() + DOWNLOAD_TIMEOUT_SECONDS
    last_progress = "0"
    last_details = ""
    last_result = "unknown"

    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        try:
            status = client.get_job_status(job_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("Stage poll failed: %s", exc)
            continue
        last_progress = status.get("progress", last_progress)
        last_details = status.get("details", "") or last_details
        last_result = status.get("result", last_result)

        if status.get("status") != "FIN":
            continue

        # Job is FIN. Verify by software list, not by <result>.
        try:
            if client.is_version_downloaded(version):
                return True, None
        except Exception as exc:  # noqa: BLE001
            log.warning("Post-job is_version_downloaded check failed: %s", exc)

        details = last_details or "no details from device"
        return False, f"download did not complete (job result={last_result}): {details}"

    return False, f"download timed out after {DOWNLOAD_TIMEOUT_SECONDS}s (progress={last_progress}%)"


def _finalize_run(
    db: Session,
    run: DeviceStageRun,
    device: Device,
    *,
    success: bool,
    error: str | None,
) -> DeviceStageRun:
    run.finished_at = datetime.now(timezone.utc)
    run.outcome = Severity.PASS if success else Severity.FAIL
    run.error = error

    if success:
        device.staged_version = run.version
        device.staged_at = run.finished_at
        device.staged_error = None
    else:
        device.staged_error = error

    db.commit()
    db.refresh(run)
    return run
