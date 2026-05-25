"""Reusable precheck core — used by both the synchronous route and Celery bulk tasks.

The HTTP path and the worker path both end up calling `run_precheck_for_device`,
which:
  1. Builds a connection (direct or via Panorama).
  2. Probes the device on the same connection so the classifier sees fresh
     ha_state, content versions, and licenses.
  3. Runs the readiness checks.
  4. Classifies each result.
  5. Persists a PrecheckRun row (optionally tied to a BulkPrecheckRun).
  6. Returns the saved row.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.enums import Severity
from app.models.precheck import PrecheckRun
from app.services.credentials import resolve as resolve_credential
from app.services.pan_client import (
    DEFAULT_READINESS_CHECKS,
    PanDeviceClient,
)
from app.services.panorama_sync import map_ha_role
from app.services.precheck_classifier import classify, overall_severity

log = logging.getLogger(__name__)


def build_client(device: Device) -> PanDeviceClient:
    """Construct the proxied or direct client for `device` based on its flags.

    Does NOT do health-check or fallback — for that use build_client_with_fallback.
    """
    if device.proxy_via_panorama:
        if not (device.panorama_id and device.panorama):
            raise ValueError("proxy_via_panorama set but device has no Panorama")
        if not device.serial:
            raise ValueError("proxy_via_panorama requires a known device serial")
        pano = device.panorama
        return PanDeviceClient.via_panorama(
            panorama_host=pano.hostname,
            panorama_cred=resolve_credential(pano.credential),
            target_serial=device.serial,
            verify_tls=pano.verify_tls,
        )

    cred = device.credential or (device.panorama.credential if device.panorama else None)
    if not cred:
        raise ValueError("Device has no credential and no Panorama-side credential available")
    return PanDeviceClient.direct(
        host=device.ip_address or device.hostname,
        cred=resolve_credential(cred),
        verify_tls=device.verify_tls,
    )


def _device_can_direct(device: Device) -> bool:
    """True if we could plausibly reach this device without Panorama."""
    if not (device.ip_address or device.hostname):
        return False
    cred = device.credential or (device.panorama.credential if device.panorama else None)
    return cred is not None


def _build_direct_client(device: Device) -> PanDeviceClient:
    cred = device.credential or (device.panorama.credential if device.panorama else None)
    if not cred:
        raise ValueError("Device has no credential for direct connect")
    return PanDeviceClient.direct(
        host=device.ip_address or device.hostname,
        cred=resolve_credential(cred),
        verify_tls=device.verify_tls,
    )


def _mark_panorama_health(db: Session, device: Device, ok: bool, error: str | None = None) -> None:
    """Record that we just succeeded / failed talking to this device's
    Panorama. The UI uses this to surface a banner and a per-Panorama badge.
    """
    from datetime import datetime, timezone
    if not (device.panorama_id and device.panorama):
        return
    pano = device.panorama
    pano.reachable = ok
    pano.last_reachability_at = datetime.now(timezone.utc)
    pano.last_reachability_error = None if ok else (error or "unreachable")
    db.commit()


def build_client_with_fallback(
    db: Session, device: Device
) -> tuple[PanDeviceClient, str]:
    """Try Panorama-proxied first (if configured) — on failure, fall back to
    direct connect when possible.

    Returns (client, route) where route is 'proxy' or 'direct'. Updates the
    Panorama's reachability fields as a side effect. Raises ValueError /
    ConnectionError if no working route exists.

    This is what probe/precheck/upgrade should use so a Panorama outage
    doesn't make individual devices unmanageable when they're reachable
    directly.
    """
    if device.proxy_via_panorama and device.panorama_id and device.serial:
        proxy_client = build_client(device)
        # Cheap health probe — one round trip. If Panorama is down it fails fast.
        try:
            proxy_client.get_system_info()
            _mark_panorama_health(db, device, ok=True)
            return proxy_client, "proxy"
        except Exception as exc:  # noqa: BLE001
            _mark_panorama_health(db, device, ok=False, error=str(exc))
            log.warning(
                "Panorama proxy to %s failed (%s); attempting direct fallback",
                device.name, exc,
            )
            if _device_can_direct(device):
                return _build_direct_client(device), "direct"
            # No fallback possible — re-raise so the caller surfaces it.
            raise ConnectionError(
                f"Panorama unreachable and no direct route available for {device.name}: {exc}"
            ) from exc

    # Not proxied (or proxy preconditions missing) — just go direct.
    return build_client(device), "direct"


def probe_device(db: Session, device: Device, client: PanDeviceClient | None = None) -> None:
    """Refresh runtime fields from the device. Caller commits."""
    if client is None:
        client = build_client(device)
    info = client.get_system_info()

    if info.serial:
        device.serial = info.serial
    if info.hostname:
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

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    device.last_refresh_at = now
    device.connected = True
    device.last_seen_at = now

    if info.ha_peer_serial:
        peer = db.query(Device).filter(Device.serial == info.ha_peer_serial).one_or_none()
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
    """Probe + run readiness checks + classify + persist. Returns the saved PrecheckRun."""
    check_names = checks or DEFAULT_READINESS_CHECKS

    # Build client first; if construction fails (including Panorama down with
    # no direct fallback), persist a failed run and return.
    try:
        client, _route = build_client_with_fallback(db, device)
    except (ValueError, ConnectionError) as exc:
        return _persist_failed_run(db, device, user_id, bulk_run_id, str(exc))

    # Probe on the same connection so the classifier sees fresh state.
    try:
        probe_device(db, device, client=client)
    except Exception as exc:  # noqa: BLE001
        log.warning("Pre-precheck probe failed for device %s: %s", device.id, exc)

    # Run checks.
    try:
        raw_results = client.run_readiness_checks(check_names)
    except Exception as exc:  # noqa: BLE001
        return _persist_failed_run(db, device, user_id, bulk_run_id, f"Pre-checks failed: {exc}")

    # Classify each.
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
