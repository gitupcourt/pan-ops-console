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

from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.command_proxy.builder import build_client_with_fallback, panorama_sw_version
from app.core.devices.models.device import Device
from app.db import SessionLocal, get_db

router = APIRouter(prefix="/upgrade/devices", tags=["upgrade"])

# The per-device fetch is almost entirely i/o-wait (a network round-trip to the
# device/Panorama), so fanning out across a small thread pool collapses an
# N-device picker open from ~N×latency to ~max(latency). Bounded so we never
# grab more than a handful of the DB pool's connections (pool_size 5 +
# max_overflow 10) at once, leaving headroom for other requests.
_BULK_FETCH_MAX_WORKERS = 8


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
    # False (default) -> each device returns its already-cached catalog (fast,
    # no update-server round-trip). True -> each device re-checks
    # updates.paloaltonetworks.com first (slow); the picker sends this only on an
    # explicit operator "check the update server" refresh.
    refresh: bool = False


class AvailableSoftwareBulkOut(BaseModel):
    """Per-device-id → response payload. We return dict-keyed-by-id so
    the frontend picker can easily build a Map for aggregation across
    selected devices.
    """

    results: dict[int, AvailableSoftwareOut]


@router.get("/{device_id}/software", response_model=AvailableSoftwareOut)
def get_available_software(
    device_id: int,
    refresh: bool = False,
    db: Session = Depends(get_db),
) -> AvailableSoftwareOut:
    """Return the version list the device itself reports as installable.

    Fast by default — returns the device's already-cached catalog. Pass
    `?refresh=true` to make the device re-check updates.paloaltonetworks.com
    first (5–30s); use it when a just-released version isn't showing yet.
    """
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")
    return _fetch_for_device(
        db, device, refresh=refresh, pano_version=panorama_sw_version(device)
    )


@router.post("/software/bulk", response_model=AvailableSoftwareBulkOut)
def get_available_software_bulk(
    payload: AvailableSoftwareBulkIn,
    db: Session = Depends(get_db),
) -> AvailableSoftwareBulkOut:
    """Bulk version-picker fetch — fast and parallel.

      - Each device returns its CACHED catalog (no update-server round-trip)
        unless `refresh` is set, so a picker open is ~1s/device instead of
        5–30s/device.
      - Devices are fetched CONCURRENTLY (each call is i/o-bound), so wall-clock
        is ~max(latency) rather than the sum.
      - The managing Panorama's version is resolved once per unique Panorama up
        front (advisory; there are far fewer Panoramas than devices).
    """
    if not payload.device_ids:
        raise HTTPException(status_code=400, detail="device_ids must be non-empty")

    devices = (
        db.execute(select(Device).where(Device.id.in_(payload.device_ids)))
        .scalars()
        .all()
    )
    found_by_id = {d.id: d for d in devices}

    # Resolve each managing Panorama's version once (deduped by panorama_id) in
    # this request session, before fanning out. panorama_sw_version is a network
    # call but Panoramas are few; doing it here keeps the per-device threads from
    # racing on a shared cache.
    pano_versions: dict[int, str | None] = {}
    for d in devices:
        if d.panorama_id is not None and d.panorama_id not in pano_versions:
            pano_versions[d.panorama_id] = panorama_sw_version(d)

    refresh = payload.refresh

    def _not_found(did: int) -> AvailableSoftwareOut:
        return AvailableSoftwareOut(
            device_id=did, device_name=f"device#{did}",
            current_version=None, available=[], error="device not found",
        )

    def _fetch_one(device_id: int) -> AvailableSoftwareOut:
        if device_id not in found_by_id:
            return _not_found(device_id)  # stale id — surface per-device
        # SQLAlchemy Sessions are NOT thread-safe, so each worker gets its own
        # session (and re-loads the device on it so relationships lazy-load on
        # the right session). The connection is held only briefly — through the
        # client build + its health-probe commit — then released for the slow
        # list_software() network call.
        thread_db = SessionLocal()
        try:
            device = thread_db.get(Device, device_id)
            if device is None:
                return _not_found(device_id)
            return _fetch_for_device(
                thread_db, device, refresh=refresh,
                pano_version=pano_versions.get(device.panorama_id),
            )
        finally:
            thread_db.close()

    results: dict[int, AvailableSoftwareOut] = {}
    workers = min(_BULK_FETCH_MAX_WORKERS, len(payload.device_ids))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one, did): did for did in payload.device_ids}
        for fut in as_completed(futures):
            did = futures[fut]
            try:
                results[did] = fut.result()
            except Exception as exc:  # noqa: BLE001 — a worker blew up unexpectedly
                results[did] = AvailableSoftwareOut(
                    device_id=did, device_name=f"device#{did}",
                    current_version=None, available=[], error=str(exc)[:500],
                )
    return AvailableSoftwareBulkOut(results=results)


def _fetch_for_device(
    db: Session, device: Device, *, refresh: bool, pano_version: str | None
) -> AvailableSoftwareOut:
    """Build the response payload for one device. Catches all network/auth
    failures and returns them in the `error` field so the frontend can render a
    per-device-row error indicator instead of failing the whole bulk call.

    `refresh` selects the device's cached catalog (False) vs a live
    update-server check (True). `pano_version` is the managing Panorama's PAN-OS
    version (resolved by the caller — once per Panorama on the bulk path; None
    if direct-attached or the lookup failed); it feeds the picker's
    version-skew warning.
    """
    base = AvailableSoftwareOut(
        device_id=device.id,
        device_name=device.name,
        current_version=device.sw_version,
        panorama_version=pano_version,
        available=[],
        error=None,
    )
    try:
        client, _route = build_client_with_fallback(db, device)
        entries = client.list_software(check_updates=refresh)
    except Exception as exc:  # noqa: BLE001 — surface to UI
        return base.model_copy(update={"error": str(exc)[:500]})

    return base.model_copy(
        update={"available": [SoftwareEntry(**e) for e in entries]}
    )
