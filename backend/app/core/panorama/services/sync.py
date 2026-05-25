"""Reusable Panorama → DB sync logic.

Used both by the on-demand /import endpoint and the scheduled Celery beat job.
Keeping it here means the HTTP layer and the worker layer call the same code.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.credentials import decrypt_key
from app.core.devices.models.device import Device
from app.core.devices.models.enums import DeviceSource
from app.core.devices.services.ha import map_ha_role
from app.core.panorama.models.panorama import Panorama
from app.core.panorama.services.client import PanoramaClient

log = logging.getLogger(__name__)


def sync_panorama(
    db: Session, pano: Panorama, *, serial_filter: list[str] | None = None
) -> list[Device]:
    """Pull devices from `pano` and upsert into the DB. Returns the touched Device rows.

    Idempotent: dedupes by serial, refreshes runtime fields, re-resolves HA peers.
    Raises on connection / API errors so callers can decide how to surface them.

    Also records Panorama reachability so the UI can show a "Panorama down"
    banner and per-device probes can know whether to even attempt the proxy
    path before falling back to direct.

    serial_filter:
      - None or empty list: import every device Panorama reports.
      - Non-empty list: only upsert devices whose serial is in this set.
        Other devices Panorama knows about are left alone — they don't get
        deleted from our DB. Used by the device-import picker in the UI.
    """
    if not pano.encrypted_api_key:
        raise ValueError(f"Panorama {pano.name} has no API key stored")
    api_key = decrypt_key(pano.encrypted_api_key)
    client = PanoramaClient(pano.hostname, api_key, verify_tls=pano.verify_tls)
    try:
        managed = client.list_managed_devices()
    except Exception as exc:
        # Mark Panorama unreachable so other callers (probe/precheck/upgrade)
        # can decide to use direct fallback.
        pano.reachable = False
        pano.last_reachability_at = datetime.now(timezone.utc)
        pano.last_reachability_error = str(exc)[:500]
        db.commit()
        raise
    # Success — Panorama is alive.
    pano.reachable = True
    pano.last_reachability_at = datetime.now(timezone.utc)
    pano.last_reachability_error = None

    serials = [m.serial for m in managed]
    existing = {
        d.serial: d for d in db.query(Device).filter(Device.serial.in_(serials)).all() if d.serial
    }

    now = datetime.now(timezone.utc)
    touched: list[Device] = []

    # Filter inputs: capacity-side feature for the device-import picker.
    # Non-empty list = only sync these serials.
    filter_set: set[str] | None = (
        set(serial_filter) if serial_filter else None
    )

    for m in managed:
        if filter_set is not None and m.serial not in filter_set:
            continue
        device = existing.get(m.serial)
        if device is None:
            device = Device(
                name=m.hostname or m.serial,
                hostname=m.hostname or m.ip_address or m.serial,
                serial=m.serial,
                source=DeviceSource.PANORAMA,
                panorama_id=pano.id,
            )
            db.add(device)
        else:
            if m.hostname:
                device.hostname = m.hostname
            device.source = DeviceSource.PANORAMA
            device.panorama_id = pano.id

        device.ip_address = m.ip_address
        device.model = m.model
        # Transitional: write to both `current_version` (upgrader name) and
        # `sw_version` (capacity name) until phase 4b's "drop sw_version"
        # cleanup. See CLAUDE.md phase 4a design contract.
        device.current_version = m.sw_version
        device.sw_version = m.sw_version
        device.device_group = m.device_group
        device.template_stack = m.template_stack
        device.connected = m.connected
        device.uptime = m.uptime
        device.ha_state = m.ha_state
        device.ha_sync_state = m.ha_sync_state
        device.ha_role = map_ha_role(m.ha_state)
        device.app_version = m.app_version
        device.threat_version = m.threat_version
        device.av_version = m.av_version
        device.wildfire_version = m.wildfire_version
        device.url_filtering_version = m.url_filtering_version
        device.gp_client_version = m.gp_client_version
        device.last_refresh_at = now
        if m.connected:
            device.last_seen_at = now

        touched.append(device)

    # Resolve HA peers now that all devices exist
    db.flush()
    by_serial = {d.serial: d for d in touched if d.serial}
    for m in managed:
        if not m.ha_peer_serial:
            continue
        me = by_serial.get(m.serial)
        peer = by_serial.get(m.ha_peer_serial)
        if me and peer:
            me.ha_peer_id = peer.id
        elif me:
            # peer not (yet) under management — clear the link rather than leave stale
            me.ha_peer_id = None

    pano.last_sync_at = now
    db.commit()
    for d in touched:
        db.refresh(d)
    return touched


def sync_all(db: Session) -> dict[int, int]:
    """Sync every configured Panorama. Returns {panorama_id: device_count_or_-1_on_error}."""
    results: dict[int, int] = {}
    for pano in db.query(Panorama).all():
        try:
            touched = sync_panorama(db, pano)
            results[pano.id] = len(touched)
            log.info("Synced %s: %d devices", pano.name, len(touched))
        except Exception:  # noqa: BLE001
            log.exception("Sync failed for Panorama %s (%s)", pano.id, pano.name)
            results[pano.id] = -1
    return results
