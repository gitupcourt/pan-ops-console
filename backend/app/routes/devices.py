"""Device CRUD, direct-to-device probe, and pre-check endpoints."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.db import get_db
from app.models.credential import Credential
from app.models.device import Device
from app.models.enums import DeviceSource, HARole
from app.models.precheck import BulkPrecheckRun, PrecheckRun
from app.models.stage import BulkStageRun, DeviceStageRun
from app.models.user import User
from app.schemas.device import DeviceCreate, DeviceOut, LatestPrecheck
from app.services import precheck as precheck_svc
from app.services.pan_client import ALL_READINESS_CHECKS, DEFAULT_READINESS_CHECKS

# Bottom of file appends disk-space helpers; keep this import in sync there.

router = APIRouter(prefix="/api/devices", tags=["devices"])
log = logging.getLogger(__name__)


class DirectDeviceCreate(BaseModel):
    """Add a standalone (non-Panorama) device by hostname + credential."""

    name: str
    hostname: str
    credential_id: int
    verify_tls: bool = True


class DevicePatch(BaseModel):
    """Partial update — only the fields you set get changed."""

    name: str | None = None
    hostname: str | None = None
    ip_address: str | None = None
    credential_id: int | None = None
    verify_tls: bool | None = None
    proxy_via_panorama: bool | None = None


class PreCheckIn(BaseModel):
    checks: list[str] | None = None  # None => DEFAULT_READINESS_CHECKS


class PreCheckOut(BaseModel):
    """Returned to the UI after a precheck run. Mirrors the persisted PrecheckRun."""

    id: int | None
    device_id: int
    ran_at: datetime
    overall_severity: str
    pass_count: int
    warn_count: int
    fail_count: int
    skip_count: int
    # results: {check_name: {raw_state, severity, reason, raw_reason}}
    results: dict[str, dict]
    error: str | None = None


@router.get("/precheck/available")
def list_available_prechecks(
    _user: User = Depends(get_current_user),
):
    """Return the canonical list of readiness checks plus our default subset."""
    return {"all": ALL_READINESS_CHECKS, "default": DEFAULT_READINESS_CHECKS}


@router.get("", response_model=list[DeviceOut])
def list_devices(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[DeviceOut]:
    devices = db.query(Device).order_by(Device.name).all()
    # Pull the latest precheck per device in one query.
    latest_by_device = _latest_precheck_by_device(db, [d.id for d in devices])
    out: list[DeviceOut] = []
    for d in devices:
        do = DeviceOut.model_validate(d)
        run = latest_by_device.get(d.id)
        if run is not None:
            do.latest_precheck = LatestPrecheck(
                id=run.id,
                ran_at=run.ran_at,
                overall_severity=run.overall_severity.value,
                pass_count=run.pass_count,
                warn_count=run.warn_count,
                fail_count=run.fail_count,
                skip_count=run.skip_count,
            )
        out.append(do)
    return out


def _latest_precheck_by_device(db: Session, device_ids: list[int]) -> dict[int, PrecheckRun]:
    """Return {device_id: most-recent PrecheckRun} for each id."""
    if not device_ids:
        return {}
    # Window function would be cleanest, but a per-id query is fine for small fleets.
    # We do one query and group on the Python side.
    rows = (
        db.query(PrecheckRun)
        .filter(PrecheckRun.device_id.in_(device_ids))
        .order_by(PrecheckRun.device_id, PrecheckRun.ran_at.desc())
        .all()
    )
    seen: dict[int, PrecheckRun] = {}
    for r in rows:
        seen.setdefault(r.device_id, r)
    return seen


@router.post("", response_model=DeviceOut, status_code=201)
def create_device(
    payload: DeviceCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Device:
    """Generic device create — used internally and for advanced cases.

    For the common 'add a standalone firewall' flow, the UI uses POST /direct."""
    device = Device(**payload.model_dump())
    db.add(device)
    db.commit()
    db.refresh(device)
    return device


@router.post("/direct", response_model=DeviceOut, status_code=201)
def create_direct_device(
    payload: DirectDeviceCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Device:
    """Add a standalone firewall, then probe it to populate fields automatically."""
    cred = db.get(Credential, payload.credential_id)
    if not cred:
        raise HTTPException(400, f"Credential {payload.credential_id} not found")

    device = Device(
        name=payload.name,
        hostname=payload.hostname,
        source=DeviceSource.DIRECT,
        credential_id=payload.credential_id,
        verify_tls=payload.verify_tls,
        ha_role=HARole.STANDALONE,
    )
    db.add(device)
    db.commit()
    db.refresh(device)

    # Best-effort probe — failures don't undo the device row, just leave fields blank.
    try:
        _probe_and_update(db, device)
    except Exception:  # noqa: BLE001
        pass

    db.refresh(device)
    return device


@router.patch("/{device_id}", response_model=DeviceOut)
def patch_device(
    device_id: int,
    payload: DevicePatch,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Device:
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Device not found")
    updates = payload.model_dump(exclude_unset=True)
    for k, v in updates.items():
        setattr(device, k, v)
    db.commit()
    db.refresh(device)
    return device


@router.delete("/{device_id}", status_code=204)
def delete_device(
    device_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Device not found")
    db.delete(device)
    db.commit()


@router.post("/{device_id}/probe", response_model=DeviceOut)
def probe_device(
    device_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Device:
    """Refresh a single device by querying it directly.

    Uses the resilient client: tries the configured route first (Panorama
    proxy if the device opts in), and on Panorama failure falls back to a
    direct connection when the device has its own credential + IP. The
    Panorama's reachability fields are updated as a side effect so the UI
    can show a banner / badge.
    """
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Device not found")
    try:
        client, _route = precheck_svc.build_client_with_fallback(db, device)
        precheck_svc.probe_device(db, device, client=client)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Probe failed: {exc}") from exc
    db.refresh(device)
    return device


@router.post("/{device_id}/precheck", response_model=PreCheckOut)
def run_precheck(
    device_id: int,
    payload: PreCheckIn = PreCheckIn(),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PreCheckOut:
    """Run readiness checks, classify each result, and persist a history row."""
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Device not found")
    run = precheck_svc.run_precheck_for_device(
        db, device, checks=payload.checks, user_id=user.id
    )
    return _run_to_out(run)


@router.get("/{device_id}/precheck/history", response_model=list[PreCheckOut])
def precheck_history(
    device_id: int,
    limit: int = 20,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[PreCheckOut]:
    if not db.get(Device, device_id):
        raise HTTPException(404, "Device not found")
    rows = (
        db.query(PrecheckRun)
        .filter(PrecheckRun.device_id == device_id)
        .order_by(PrecheckRun.ran_at.desc())
        .limit(limit)
        .all()
    )
    return [_run_to_out(r) for r in rows]


@router.get("/precheck/runs/{run_id}", response_model=PreCheckOut)
def get_precheck_run(
    run_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> PreCheckOut:
    """Fetch a specific pre-check run by id.

    Used by JobDetail to expand inline what a precheck/postcheck saw, since
    upgrade tasks store only the run id on task.progress.
    """
    run = db.get(PrecheckRun, run_id)
    if not run:
        raise HTTPException(404, "PrecheckRun not found")
    return _run_to_out(run)


# ---------- bulk pre-checks ----------


class BulkPreCheckIn(BaseModel):
    device_ids: list[int]
    checks: list[str] | None = None


class BulkPreCheckSummary(BaseModel):
    bulk_run_id: int
    started_at: datetime
    finished_at: datetime | None
    target_count: int
    completed_count: int
    pending_count: int
    pass_count: int
    warn_count: int
    fail_count: int
    error_count: int
    cancelled: bool
    # Per-device latest result keyed by device_id
    results: dict[int, PreCheckOut]


@router.post("/precheck/bulk", response_model=BulkPreCheckSummary, status_code=202)
def start_bulk_precheck(
    payload: BulkPreCheckIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BulkPreCheckSummary:
    """Kick off pre-checks across N devices in parallel via Celery. Returns a
    bulk_run_id the UI can poll."""
    if not payload.device_ids:
        raise HTTPException(400, "device_ids must be non-empty")

    devices = db.query(Device).filter(Device.id.in_(payload.device_ids)).all()
    if len(devices) != len(payload.device_ids):
        found = {d.id for d in devices}
        missing = sorted(set(payload.device_ids) - found)
        raise HTTPException(400, f"Unknown device ids: {missing}")

    bulk = BulkPrecheckRun(
        started_by_user_id=user.id,
        target_count=len(devices),
        checks_requested=payload.checks or DEFAULT_READINESS_CHECKS,
    )
    db.add(bulk)
    db.commit()
    db.refresh(bulk)

    # Dispatch one Celery task per device. They run in parallel up to worker concurrency.
    from app.tasks.precheck import run_device_precheck_task
    for d in devices:
        run_device_precheck_task.delay(d.id, bulk.id, payload.checks, user.id)

    return _bulk_summary(db, bulk)


@router.get("/precheck/bulk/{bulk_id}", response_model=BulkPreCheckSummary)
def get_bulk_precheck(
    bulk_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> BulkPreCheckSummary:
    bulk = db.get(BulkPrecheckRun, bulk_id)
    if not bulk:
        raise HTTPException(404, "Bulk run not found")
    return _bulk_summary(db, bulk)


def _bulk_summary(db: Session, bulk: BulkPrecheckRun) -> BulkPreCheckSummary:
    runs = (
        db.query(PrecheckRun)
        .filter(PrecheckRun.bulk_run_id == bulk.id)
        .order_by(PrecheckRun.device_id, PrecheckRun.ran_at.desc())
        .all()
    )
    # one per device — first-seen wins because of order_by ran_at DESC
    by_device: dict[int, PrecheckRun] = {}
    for r in runs:
        by_device.setdefault(r.device_id, r)

    completed = len(by_device)
    pass_n = sum(1 for r in by_device.values() if r.overall_severity.value == "pass")
    warn_n = sum(1 for r in by_device.values() if r.overall_severity.value == "warn")
    fail_n = sum(1 for r in by_device.values() if r.overall_severity.value == "fail" and not r.error)
    err_n  = sum(1 for r in by_device.values() if r.error)

    # Mark finished_at when all targets done (idempotent).
    if completed >= bulk.target_count and bulk.finished_at is None:
        from datetime import datetime, timezone
        bulk.finished_at = datetime.now(timezone.utc)
        db.commit()

    return BulkPreCheckSummary(
        bulk_run_id=bulk.id,
        started_at=bulk.started_at,
        finished_at=bulk.finished_at,
        target_count=bulk.target_count,
        completed_count=completed,
        pending_count=bulk.target_count - completed,
        pass_count=pass_n,
        warn_count=warn_n,
        fail_count=fail_n,
        error_count=err_n,
        cancelled=bulk.cancelled,
        results={d_id: _run_to_out(r) for d_id, r in by_device.items()},
    )


def _run_to_out(r: PrecheckRun) -> PreCheckOut:
    return PreCheckOut(
        id=r.id,
        device_id=r.device_id,
        ran_at=r.ran_at,
        overall_severity=r.overall_severity.value,
        pass_count=r.pass_count,
        warn_count=r.warn_count,
        fail_count=r.fail_count,
        skip_count=r.skip_count,
        results=r.results or {},
        error=r.error,
    )


@router.post("/import/csv", response_model=list[DeviceOut])
def import_csv(
    _db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """TODO: parse uploaded CSV (UploadFile), create Devices, dedupe by serial/hostname."""
    raise HTTPException(501, "CSV import not yet implemented")


# ---------- software inventory (versions available for download) ----------


class SoftwareEntry(BaseModel):
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
    current_version: str | None
    available: list[SoftwareEntry]
    error: str | None = None


class AvailableSoftwareBulkIn(BaseModel):
    device_ids: list[int]


class AvailableSoftwareBulkOut(BaseModel):
    results: dict[int, AvailableSoftwareOut]


@router.get("/{device_id}/software/available", response_model=AvailableSoftwareOut)
def get_available_software(
    device_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> AvailableSoftwareOut:
    """Return the version list the device itself reports as installable.

    The device only lists model-compatible versions, so this is implicitly
    platform-aware — no need for us to hardcode a model -> version table.
    """
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Device not found")
    return _fetch_available(device)


@router.post("/software/available/bulk", response_model=AvailableSoftwareBulkOut)
def get_available_software_bulk(
    payload: AvailableSoftwareBulkIn,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> AvailableSoftwareBulkOut:
    """Bulk version of the above — used by the version-picker modal."""
    if not payload.device_ids:
        raise HTTPException(400, "device_ids must be non-empty")
    devices = db.query(Device).filter(Device.id.in_(payload.device_ids)).all()
    return AvailableSoftwareBulkOut(
        results={d.id: _fetch_available(d) for d in devices}
    )


def _fetch_available(device: Device) -> AvailableSoftwareOut:
    try:
        client = precheck_svc.build_client(device)
        entries = client.list_software()
    except Exception as exc:  # noqa: BLE001
        return AvailableSoftwareOut(
            device_id=device.id,
            current_version=device.current_version,
            available=[],
            error=str(exc),
        )
    return AvailableSoftwareOut(
        device_id=device.id,
        current_version=device.current_version,
        available=[
            SoftwareEntry(
                version=e.get("version") or "",
                downloaded=bool(e.get("downloaded")),
                current=bool(e.get("current")),
                latest=bool(e.get("latest")),
                uploaded=bool(e.get("uploaded")),
                filename=e.get("filename"),
                released_on=e.get("released_on"),
                size_kb=e.get("size_kb"),
            )
            for e in entries
            if e.get("version")
        ],
    )


# ---------- pre-stage (download PAN-OS image, do not install) ----------


class StageIn(BaseModel):
    version: str


class BulkStageIn(BaseModel):
    device_ids: list[int]
    version: str


class StageRunOut(BaseModel):
    id: int
    device_id: int
    version: str
    started_at: datetime
    finished_at: datetime | None
    outcome: str
    error: str | None

    class Config:
        from_attributes = True


class BulkStageSummary(BaseModel):
    bulk_run_id: int
    started_at: datetime
    finished_at: datetime | None
    target_count: int
    completed_count: int
    pending_count: int
    success_count: int
    failure_count: int
    cancelled: bool
    version: str
    results: dict[int, StageRunOut]


@router.post("/{device_id}/stage", response_model=StageRunOut, status_code=202)
def stage_one(
    device_id: int,
    payload: StageIn,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> StageRunOut:
    """Kick off staging a single device asynchronously. Poll the device row
    (Device.staged_version / staged_error) for the outcome."""
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Device not found")

    from app.tasks.stage import run_device_stage_task
    run_device_stage_task.delay(device_id, payload.version, None)

    # Return a placeholder run so the UI gets immediate feedback.
    return StageRunOut(
        id=0, device_id=device_id, version=payload.version,
        started_at=datetime.now(timezone.utc), finished_at=None,
        outcome="skip", error=None,
    )


@router.post("/stage/bulk", response_model=BulkStageSummary, status_code=202)
def start_bulk_stage(
    payload: BulkStageIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BulkStageSummary:
    if not payload.device_ids:
        raise HTTPException(400, "device_ids must be non-empty")
    if not payload.version.strip():
        raise HTTPException(400, "version is required")

    devices = db.query(Device).filter(Device.id.in_(payload.device_ids)).all()
    if len(devices) != len(payload.device_ids):
        found = {d.id for d in devices}
        missing = sorted(set(payload.device_ids) - found)
        raise HTTPException(400, f"Unknown device ids: {missing}")

    bulk = BulkStageRun(
        started_by_user_id=user.id,
        target_count=len(devices),
        version=payload.version,
    )
    db.add(bulk)
    db.commit()
    db.refresh(bulk)

    from app.tasks.stage import run_device_stage_task
    for d in devices:
        run_device_stage_task.delay(d.id, payload.version, bulk.id)

    return _bulk_stage_summary(db, bulk)


@router.get("/stage/bulk/{bulk_id}", response_model=BulkStageSummary)
def get_bulk_stage(
    bulk_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> BulkStageSummary:
    bulk = db.get(BulkStageRun, bulk_id)
    if not bulk:
        raise HTTPException(404, "Bulk stage run not found")
    return _bulk_stage_summary(db, bulk)


def _bulk_stage_summary(db: Session, bulk: BulkStageRun) -> BulkStageSummary:
    runs = (
        db.query(DeviceStageRun)
        .filter(DeviceStageRun.bulk_run_id == bulk.id)
        .order_by(DeviceStageRun.device_id, DeviceStageRun.started_at.desc())
        .all()
    )
    by_device: dict[int, DeviceStageRun] = {}
    for r in runs:
        by_device.setdefault(r.device_id, r)

    finished_runs = [r for r in by_device.values() if r.finished_at is not None]
    completed = len(finished_runs)
    success = sum(1 for r in finished_runs if r.outcome.value == "pass")
    failure = sum(1 for r in finished_runs if r.outcome.value != "pass")

    if completed >= bulk.target_count and bulk.finished_at is None:
        bulk.finished_at = datetime.now(timezone.utc)
        db.commit()

    return BulkStageSummary(
        bulk_run_id=bulk.id,
        started_at=bulk.started_at,
        finished_at=bulk.finished_at,
        target_count=bulk.target_count,
        completed_count=completed,
        pending_count=bulk.target_count - completed,
        success_count=success,
        failure_count=failure,
        cancelled=bulk.cancelled,
        version=bulk.version,
        results={
            d_id: StageRunOut(
                id=r.id,
                device_id=r.device_id,
                version=r.version,
                started_at=r.started_at,
                finished_at=r.finished_at,
                outcome=r.outcome.value,
                error=r.error,
            )
            for d_id, r in by_device.items()
        },
    )


# ---------- disk-space self-help ----------


class DiskSpaceOut(BaseModel):
    """One filesystem row from `show system disk-space`."""

    filesystem: str
    size: str
    used: str
    avail: str
    use_pct: str
    mounted_on: str


@router.get("/{device_id}/disk-space", response_model=list[DiskSpaceOut])
def get_disk_space(
    device_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Live disk-space readout from the device.

    Used by the disk-space self-help panel: shows the operator how tight
    the device's `/opt/panrepo` is and lets them prune old images before
    starting a download that would otherwise fail mid-stream."""
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Device not found")
    try:
        client, _route = precheck_svc.build_client_with_fallback(db, device)
        return client.get_disk_space()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"disk-space query failed: {exc}") from exc


class ImageDeleteIn(BaseModel):
    version: str


@router.post("/{device_id}/dns-resolve")
def dns_resolve(
    device_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Cheap, repeatable DNS-only lookup from inside the backend container.

    Useful for confirming that a DNS-cache flush (Docker Desktop restart,
    AD DNS refresh, etc.) actually took effect — full connectivity tests
    are slow because they wait for TCP/TLS/API. This is the same first
    step run in isolation, with the container's resolver state attached
    so the operator can see WHY it's still answering with the old IP."""
    import socket
    from app.services.connectivity import (
        _check_docker_collision,
        _read_resolver_state,
    )

    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Device not found")
    host = (device.ip_address or device.hostname or "").strip()
    if not host:
        raise HTTPException(400, "Device has no ip_address or hostname set")

    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        resolved_ip = infos[0][4][0] if infos else None
    except socket.gaierror as exc:
        return {
            "host": host,
            "resolved_ip": None,
            "error": str(exc),
            "collision_hint": None,
            "resolver_state": _read_resolver_state(),
        }
    return {
        "host": host,
        "resolved_ip": resolved_ip,
        "error": None,
        "collision_hint": _check_docker_collision(resolved_ip) if resolved_ip else None,
        "resolver_state": _read_resolver_state(),
    }


@router.post("/{device_id}/connectivity-test")
def connectivity_test(
    device_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Run a 4-step network/auth diagnostic from inside the worker container.

    Sequential DNS → TCP/443 → TLS → API-keygen, each reported separately
    with latency and a human-readable hint on what to check next. Pure
    diagnostic — does not modify any device state."""
    from app.services.connectivity import run_connectivity_test

    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Device not found")
    return {"steps": run_connectivity_test(device)}


@router.post("/{device_id}/software/delete")
def delete_software_image(
    device_id: int,
    payload: ImageDeleteIn,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Delete a downloaded PAN-OS image from the device.

    Safety belt: refuse to delete the currently-running version locally
    rather than rely on the device's own guard, since the local check is
    cheap and the operator confirmation flow is cleaner without a server
    round-trip just to learn we can't.

    After the delete, re-probe to refresh `downloaded_versions` so the UI
    chip count updates without the operator having to hit Probe themselves.
    """
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Device not found")
    if device.current_version and payload.version == device.current_version:
        raise HTTPException(400, "Refusing to delete the currently-running PAN-OS version")
    try:
        client, _route = precheck_svc.build_client_with_fallback(db, device)
        client.delete_software_image(payload.version)
        precheck_svc.probe_device(db, device, client=client)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"image delete failed: {exc}") from exc
    return {"ok": True, "deleted": payload.version}



