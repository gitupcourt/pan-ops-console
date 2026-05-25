"""Pre-check service — probe + readiness checks + classify + persist.

Used by both the synchronous /devices/{id}/precheck route and the bulk
Celery task. Both paths funnel into `run_precheck_for_device`, which:

  1. Builds a connection (proxy-first, direct-fallback via
     `core.command_proxy.build_client_with_fallback`).
  2. Probes the device on the same connection so the classifier sees
     fresh ha_state, content versions, and licenses.
  3. Runs `pan-os-upgrade-assurance`'s readiness checks.
  4. Classifies each result through `precheck_classifier.classify`.
  5. Persists a PrecheckRun row (optionally tied to a BulkPrecheckRun).
  6. Returns the saved row.

Carry-forward from pan-fw-upgrader's `services/precheck.py`. The build-
client portion of that file already moved to `core.command_proxy` in
phase 4b; this is the remaining application logic.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.command_proxy import build_client_with_fallback
from app.core.command_proxy.pan_client import (
    DEFAULT_READINESS_CHECKS,
    PanDeviceClient,
)
from app.core.devices.models.device import Device
from app.core.devices.services.ha import map_ha_role
from app.upgrade.models.enums import Severity
from app.upgrade.models.precheck import PrecheckRun
from app.upgrade.services.precheck_classifier import classify, overall_severity

log = logging.getLogger(__name__)


def probe_device(
    db: Session, device: Device, client: PanDeviceClient | None = None
) -> None:
    """Refresh runtime fields from the device. Caller commits.

    Pulls system info, licenses, and downloaded-software list onto the
    Device row. License + software calls are best-effort: a failure
    there doesn't fail the probe; the system-info call is the
    authoritative one (its failure raises).

    Side effect: links HA peer rows. If the probe returns
    `ha_peer_serial` and we have a Device with that serial, we set the
    `ha_peer_id` self-FK. The peer's link to us is set when *it* gets
    probed in turn (the orchestrator probes both members of a pair).
    """
    if client is None:
        client, _route = build_client_with_fallback(db, device)
    info = client.get_system_info()

    if info.serial:
        device.serial = info.serial
    if info.hostname:
        # If the human-readable name was just the bare hostname or serial,
        # prefer the device's own reported hostname. Don't overwrite a
        # deliberate custom name.
        if device.name == device.hostname or device.name == device.serial:
            device.name = info.hostname
    device.model = info.model
    device.current_version = info.sw_version
    device.uptime = info.uptime
    device.app_version = info.app_version
    device.threat_version = info.threat_version
    device.av_version = info.av_version
    device.wildfire_version = info.wildfire_version
    device.url_filtering_version = info.url_filtering_version
    device.gp_client_version = info.gp_client_version
    device.ha_state = info.ha_state
    device.ha_role = map_ha_role(info.ha_state)
    device.ha_sync_state = info.ha_sync_state

    now = datetime.now(timezone.utc)
    device.last_refresh_at = now
    device.connected = True
    device.last_seen_at = now

    if info.ha_peer_serial:
        peer = (
            db.query(Device).filter(Device.serial == info.ha_peer_serial).one_or_none()
        )
        if peer:
            device.ha_peer_id = peer.id

    try:
        device.licenses = client.get_licenses()
    except Exception:  # noqa: BLE001
        pass

    # Snapshot which PAN-OS images are currently on the device's software
    # partition — feeds the "already downloaded" hints in the UI and (later)
    # the disk-cleanup flow. Best-effort; don't fail the probe if this errors.
    try:
        software = client.list_software()
        versions = {
            e.get("version")
            for e in software
            if e.get("version") and (e.get("downloaded") or e.get("current"))
        }
        device.downloaded_versions = sorted(versions)
    except Exception:  # noqa: BLE001
        pass


def run_precheck_for_device(
    db: Session,
    device: Device,
    *,
    checks: list[str] | None = None,
    user_id: int | None = None,
    bulk_run_id: int | None = None,
) -> PrecheckRun:
    """Probe + run readiness checks + classify + persist.

    Returns the saved PrecheckRun. Persists a failed-run row even when
    the connection itself fails — the UI always has a row to render
    instead of a missing one.
    """
    check_names = checks or DEFAULT_READINESS_CHECKS

    try:
        client, _route = build_client_with_fallback(db, device)
    except (ValueError, ConnectionError) as exc:
        return _persist_failed_run(db, device, user_id, bulk_run_id, str(exc))

    # Probe on the same connection so the classifier sees fresh state.
    try:
        probe_device(db, device, client=client)
    except Exception as exc:  # noqa: BLE001
        log.warning("Pre-precheck probe failed for device %s: %s", device.id, exc)

    try:
        raw_results = client.run_readiness_checks(check_names)
    except Exception as exc:  # noqa: BLE001
        return _persist_failed_run(
            db, device, user_id, bulk_run_id, f"Pre-checks failed: {exc}"
        )

    classified: dict[str, dict] = {}
    for name, raw in raw_results.items():
        sev, reason = classify(name, raw, device)
        classified[name] = {
            "raw_state": bool(raw.get("state")),
            "raw_reason": str(raw.get("reason", "")),
            "severity": sev.value,
            "reason": reason,
        }

    sevs = [Severity(c["severity"]) for c in classified.values()]
    overall = overall_severity(sevs) if sevs else Severity.FAIL

    run = PrecheckRun(
        device_id=device.id,
        bulk_run_id=bulk_run_id,
        ran_by_user_id=user_id,
        overall_severity=overall,
        pass_count=sum(1 for s in sevs if s == Severity.PASS),
        warn_count=sum(1 for s in sevs if s == Severity.WARN),
        fail_count=sum(1 for s in sevs if s == Severity.FAIL),
        skip_count=sum(1 for s in sevs if s == Severity.SKIP),
        results=classified,
        error=None,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _persist_failed_run(
    db: Session,
    device: Device,
    user_id: int | None,
    bulk_run_id: int | None,
    error: str,
) -> PrecheckRun:
    """Write a PrecheckRun row marking the whole run as FAIL with the
    supplied error message. Used when we couldn't even reach the device
    or the library raised wholesale.
    """
    run = PrecheckRun(
        device_id=device.id,
        bulk_run_id=bulk_run_id,
        ran_by_user_id=user_id,
        overall_severity=Severity.FAIL,
        results={},
        error=error,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run
