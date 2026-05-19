"""Pull devices from Panorama and upsert them into our DB.

Same shape as pan-fw-upgrader's panorama_sync — minus the upgrade-specific
fields (HA roles, content versions, staged images). Capacity analyzer cares
about who/where/what-model and trusts Panorama for that.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.device import Device
from app.models.enums import DeviceSource
from app.models.panorama import Panorama
from app.services.credentials import resolve as resolve_credential
from app.services.panorama_client import PanoramaClient

log = logging.getLogger(__name__)


def sync_panorama(db: Session, pano: Panorama) -> list[Device]:
    cred = resolve_credential(pano.credential)
    client = PanoramaClient(pano.hostname, cred, verify_tls=pano.verify_tls)
    try:
        managed = client.list_managed_devices()
    except Exception as exc:
        pano.reachable = False
        pano.last_reachability_at = datetime.now(timezone.utc)
        pano.last_reachability_error = str(exc)[:500]
        db.commit()
        raise

    pano.reachable = True
    pano.last_reachability_at = datetime.now(timezone.utc)
    pano.last_reachability_error = None

    serials = [m.serial for m in managed]
    existing = {
        d.serial: d for d in db.query(Device).filter(Device.serial.in_(serials)).all() if d.serial
    }

    now = datetime.now(timezone.utc)
    touched: list[Device] = []

    for m in managed:
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
        device.sw_version = m.sw_version
        touched.append(device)

    pano.last_sync_at = now
    db.commit()
    for d in touched:
        db.refresh(d)
    return touched
