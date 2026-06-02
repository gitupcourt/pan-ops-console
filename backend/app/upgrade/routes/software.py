"""Per-device software-availability endpoints — feeds the version picker
in the upgrade JobForm.

The device's own "request system software check" → "info" returns the
list of versions the device is willing to install (model-compatible by
definition — PA-220 won't report PAN-OS 12.x). That makes this the
right source of truth for the version picker instead of trying to
maintain a model → versions table in our own code.

  GET  /upgrade/devices/{id}/software            — one device
  POST /upgrade/devices/software/bulk            — many devices

Both proxy through `build_client_with_fallback` so the proxy-by-default
policy applies. The XML round-trip is slow (the device contacts
updates.paloaltonetworks.com), so the picker should call this once per
selection-change and cache the result client-side.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.command_proxy.builder import build_client_with_fallback, panorama_sw_version
from app.core.devices.models.device import Device
from app.db import get_db

router = APIRouter(prefix="/upgrade/devices", tags=["upgrade"])


class SoftwareEntry(BaseModel):
    """One row from `request system software info`. Mirrors what
    `PanDeviceClient.list_software()` returns; documented there.
    """

    version: str
    downloaded: bool
    current: bool
    latest: bool
    uploaded: bool
    filename: str | None
    released_on: str | None
    size_kb: str | None


class AvailableSoftwareOut(BaseModel):
    device_id: int
    device_name: str
    current_version: str | None
    # The managing Panorama's PAN-OS version (None if direct-attached or the
    # lookup failed). The picker compares the selected target against this to
    # warn when the target would put the firewall AHEAD of Panorama
    # (unsupported by PAN-OS; breaks post-upgrade operations via Panorama).
    panorama_version: str | None = None
    available: list[SoftwareEntry]
    # Populated when the check-now / info call to the device fails
    # (network down, Panorama unreachable, credentials missing, etc).
    # In that case `available` is empty.
    error: str | None = None


class AvailableSoftwareBulkIn(BaseModel):
    device_ids: list[int]


class AvailableSoftwareBulkOut(BaseModel):
    """Per-device-id → response payload. We return dict-keyed-by-id so
    the frontend picker can easily build a Map for aggregation across
    selected devices.
    """

    results: dict[int, AvailableSoftwareOut]


@router.get("/{device_id}/software", response_model=AvailableSoftwareOut)
def get_available_software(
    device_id: int,
    db: Session = Depends(get_db),
) -> AvailableSoftwareOut:
    """Return the version list the device itself reports as installable.

    Slow — the device may take 5–30s to refresh its software catalog
    from updates.paloaltonetworks.com. Callers should cache.
    """
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")
    return _fetch_for_device(db, device)


@router.post("/software/bulk", response_model=AvailableSoftwareBulkOut)
def get_available_software_bulk(
    payload: AvailableSoftwareBulkIn,
    db: Session = Depends(get_db),
) -> AvailableSoftwareBulkOut:
    """Bulk version of the above — used by the version-picker in JobForm.

    Fetches sequentially. With the realistic operator scale (typically
    1–10 devices per upgrade job), 5–30s per device adds up but is
    tolerable. If this becomes painful at larger fleets, parallelize
    with a ThreadPoolExecutor here — every device's call is i/o bound.
    """
    if not payload.device_ids:
        raise HTTPException(status_code=400, detail="device_ids must be non-empty")

    devices = (
        db.execute(select(Device).where(Device.id.in_(payload.device_ids)))
        .scalars()
        .all()
    )
    found_by_id = {d.id: d for d in devices}
    results: dict[int, AvailableSoftwareOut] = {}
    pano_cache: dict[int, str | None] = {}  # panorama_id -> version, this request
    for did in payload.device_ids:
        device = found_by_id.get(did)
        if device is None:
            # Operator sent a stale id — surface per-device so the rest
            # of the picker still works.
            results[did] = AvailableSoftwareOut(
                device_id=did,
                device_name=f"device#{did}",
                current_version=None,
                available=[],
                error="device not found",
            )
            continue
        results[did] = _fetch_for_device(db, device, pano_cache=pano_cache)
    return AvailableSoftwareBulkOut(results=results)


def _fetch_for_device(
    db: Session, device: Device, pano_cache: dict[int, str | None] | None = None
) -> AvailableSoftwareOut:
    """Build the response payload for one device. Catches all
    network/auth failures and returns them in the `error` field so the
    frontend can render a per-device-row error indicator instead of
    failing the whole bulk call.

    `pano_cache` (bulk path) memoizes the managing Panorama's version by
    panorama_id so an HA pair / fleet sharing one Panorama isn't re-queried
    once per device.
    """
    base = AvailableSoftwareOut(
        device_id=device.id,
        device_name=device.name,
        current_version=device.sw_version,
        panorama_version=None,
        available=[],
        error=None,
    )
    try:
        client, _route = build_client_with_fallback(db, device)
        entries = client.list_software()
    except Exception as exc:  # noqa: BLE001 — surface to UI
        return base.model_copy(update={"error": str(exc)[:500]})

    # Managing Panorama's version (best-effort, advisory — feeds the version-
    # skew warning in the picker). Cached per Panorama within a bulk request.
    pid = device.panorama_id
    if pano_cache is not None and pid is not None and pid in pano_cache:
        pano_ver = pano_cache[pid]
    else:
        pano_ver = panorama_sw_version(device)
        if pano_cache is not None and pid is not None:
            pano_cache[pid] = pano_ver

    return base.model_copy(
        update={
            "available": [SoftwareEntry(**e) for e in entries],
            "panorama_version": pano_ver,
        }
    )
