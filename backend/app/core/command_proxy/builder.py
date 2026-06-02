"""Proxy-first / direct-fallback PanDeviceClient builder.

Lifted at phase 4b from pan-fw-upgrader's `services/precheck.py`. Same
contract as the upgrader-session's MIGRATION_NOTES §4 describes, adapted
for the merged app's inline `encrypted_api_key` credential pattern
(capacity's storage shape, retained as part of phase 4a's "deferred
credentials-table reconciliation" decision).

## Contract

`build_client_with_fallback(db, device) -> (PanDeviceClient, route)`
where `route ∈ {"proxy", "direct"}`.

Preconditions:
- `device.panorama` is eagerly loadable (it's `lazy="joined"` on the
  relationship already, so this is true).
- The Panorama's credential is decryptable (i.e., FERNET_KEY matches
  what encrypted it).
- For direct fallback to be possible: `device.ip_address` (or
  `hostname`) is set AND `device.encrypted_api_key` is non-null.

Behavior:
1. If `proxy_via_panorama && panorama_id && serial`: build a proxy
   client, do a cheap `get_system_info()` health probe.
2. On proxy success: mark Panorama healthy, return proxy.
3. On proxy probe raising `TargetDisconnectedError` (Panorama reachable,
   but it reports the device as offline): mark Panorama HEALTHY (the
   proxy call did reach Panorama), then try direct fallback if available,
   else re-raise. Critically, this does *not* taint
   `panoramas.reachable` — the Panorama is fine, the device is down.
4. On any other proxy failure (network error to Panorama, auth rejected,
   etc.): mark Panorama unhealthy (with exception text in
   `last_reachability_error`), log a warning, then try direct fallback.
5. If direct fallback available: return direct client.
6. If no direct fallback possible: re-raise.

Side effect to know about (per MIGRATION_NOTES §4):
- Writes to `panoramas.reachable`, `panoramas.last_reachability_at`,
  `panoramas.last_reachability_error` via `_mark_panorama_health`.
  The helper commits, so the caller does not need to.

Error-attribution fix (post-#43, post-proxy-by-default rollout): an
earlier version of this routine treated *every* probe failure as
"Panorama unhealthy", which caused Panorama's reachability flag to
flap False whenever a single device behind it was offline (an N×1
attribution bug — every poll of every disconnected device wrote
`reachable=False` for the shared Panorama). The TargetDisconnectedError
branch above keeps that flag accurate when the failure is at the
device layer rather than the Panorama layer.

Notable calibration choices preserved verbatim:
- Health probe is `get_system_info()` specifically because it's the
  cheapest XML-API call that proves both connectivity AND auth.
  Don't swap for `op('show system info')` — different code path,
  masks credential errors.
- Probe failure logged at WARNING, not ERROR, because direct
  fallback usually succeeds. ERROR would spam logs during normal
  Panorama-restart windows.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.command_proxy.pan_client import PanDeviceClient, TargetDisconnectedError
from app.core.credentials import decrypt_key
from app.core.devices.models.device import Device

log = logging.getLogger(__name__)


def _device_can_direct(device: Device) -> bool:
    """True if we could plausibly reach this device without Panorama.

    Capacity's inline credential pattern: needs both a target address
    (ip_address or hostname) AND the device's own encrypted_api_key.
    Unlike the upgrader's "fall back to Panorama's credential" path,
    capacity doesn't reuse the Panorama's key for direct device
    connections.
    """
    if not (device.ip_address or device.hostname):
        return False
    return bool(device.encrypted_api_key)


def _build_proxy_client(device: Device) -> PanDeviceClient:
    pano = device.panorama
    if pano is None:
        raise ValueError(
            f"Device {device.name}: proxy_via_panorama set but no Panorama linked"
        )
    if not pano.encrypted_api_key:
        raise ValueError(
            f"Panorama {pano.name} has no API key stored — cannot proxy"
        )
    if not device.serial:
        raise ValueError(
            f"Device {device.name}: proxy_via_panorama requires a known serial"
        )
    api_key = decrypt_key(pano.encrypted_api_key, purpose=f"panorama:{pano.id}")
    return PanDeviceClient.via_panorama(
        panorama_host=pano.hostname,
        panorama_api_key=api_key,
        target_serial=device.serial,
        verify_tls=pano.verify_tls,
        timeout_s=get_settings().PAN_CLIENT_TIMEOUT_SECONDS,
    )


def _build_direct_client(device: Device) -> PanDeviceClient:
    if not device.encrypted_api_key:
        raise ValueError(
            f"Device {device.name} has no API key for direct connection"
        )
    api_key = decrypt_key(device.encrypted_api_key, purpose=f"device:{device.id}")
    target = device.ip_address or device.hostname
    return PanDeviceClient.direct(
        host=target,
        api_key=api_key,
        verify_tls=device.verify_tls,
        timeout_s=get_settings().PAN_CLIENT_TIMEOUT_SECONDS,
    )


def _mark_panorama_health(
    db: Session, device: Device, *, ok: bool, error: str | None = None
) -> None:
    """Record success/failure talking to this device's Panorama.

    Commits before returning — keeps the per-Panorama reachability
    signal consistent across concurrent pollers. Idempotent: if the
    device has no Panorama, this is a no-op.
    """
    if not (device.panorama_id and device.panorama):
        return
    pano = device.panorama
    pano.reachable = ok
    pano.last_reachability_at = datetime.now(timezone.utc)
    pano.last_reachability_error = None if ok else (error or "unreachable")[:500]
    db.commit()


def build_client_with_fallback(
    db: Session, device: Device
) -> tuple[PanDeviceClient, str]:
    """Try Panorama-proxied first (if configured); fall back to direct on failure.

    See module docstring for the full contract.
    """
    if device.proxy_via_panorama and device.panorama_id and device.serial:
        proxy_client = _build_proxy_client(device)
        # Cheap, auth-proving health probe. If Panorama is down, fails fast.
        try:
            proxy_client.get_system_info()
            _mark_panorama_health(db, device, ok=True)
            return proxy_client, "proxy"
        except TargetDisconnectedError as exc:
            # Panorama itself is healthy — it answered the proxy call. It
            # just told us the target firewall is offline. Mark Panorama
            # as reachable (the call succeeded at the Panorama layer) but
            # propagate the device-down state so the caller can surface
            # "device disconnected" rather than "Panorama proxy failed."
            _mark_panorama_health(db, device, ok=True)
            log.info(
                "Panorama reports %s as disconnected; not attempting fallback",
                device.name,
            )
            # Direct fallback is unlikely to succeed for a Panorama-imported
            # device that's offline (no own key, and the device is down anyway).
            # But if the operator has set up direct credentials, give it a
            # shot — sometimes Panorama's "not connected" reflects a stale
            # connection while the device itself is actually reachable.
            if _device_can_direct(device):
                log.info(
                    "Trying direct fallback for %s despite Panorama-reported disconnect",
                    device.name,
                )
                return _build_direct_client(device), "direct"
            raise
        except Exception as exc:  # noqa: BLE001
            _mark_panorama_health(db, device, ok=False, error=str(exc))
            log.warning(
                "Panorama proxy to %s failed (%s); attempting direct fallback",
                device.name, exc,
            )
            if _device_can_direct(device):
                return _build_direct_client(device), "direct"
            raise ConnectionError(
                f"Panorama unreachable and no direct route available "
                f"for {device.name}: {exc}"
            ) from exc

    # Not proxied (or preconditions not met) — go direct.
    return _build_direct_client(device), "direct"


def panorama_sw_version(device: Device) -> str | None:
    """Best-effort PAN-OS version of the device's managing Panorama
    (`show system info` → sw-version), or None if the device isn't
    Panorama-managed or the query fails.

    Advisory use only — the version-skew warning (PAN-OS requires Panorama to
    run >= its firewalls, so a target that outranks Panorama is unsupported and
    breaks post-upgrade ops via Panorama). Never blocks anything; swallows all
    errors and returns None.
    """
    pano = getattr(device, "panorama", None)
    if not (device.proxy_via_panorama and pano and pano.encrypted_api_key):
        return None
    try:
        from panos.panorama import Panorama as _Panorama  # noqa: PLC0415

        key = decrypt_key(pano.encrypted_api_key, purpose=f"panorama:{pano.id}")
        p = _Panorama(
            pano.hostname, api_key=key, timeout=get_settings().PAN_CLIENT_TIMEOUT_SECONDS
        )
        p.verify_ssl = pano.verify_tls
        resp = p.op("<show><system><info></info></system></show>", cmd_xml=False)
        return resp.findtext(".//sw-version") or None
    except Exception as exc:  # noqa: BLE001
        log.info("Panorama version lookup skipped for %s: %s", device.name, exc)
        return None
