"""Disk-space cleanup endpoints for a device.

  GET  /upgrade/devices/{id}/disk-cleanup/plan   — dry-run: usage + candidates
  POST /upgrade/devices/{id}/disk-cleanup         — delete approved images + cleanup

Surfaced on the precheck disk-space alert and the Inventory row action. The
plan endpoint is a pure read (safe to call on open); the POST is destructive
and re-validates the requested versions against a freshly-recomputed safe set
server-side (see services/disk_cleanup.py). Both proxy through
`build_client_with_fallback`, so the proxy-by-default policy applies.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth.deps import current_user
from app.core.auth.models.user import User
from app.core.devices.models.device import Device
from app.db import get_db
from app.upgrade.services import disk_cleanup as svc

router = APIRouter(prefix="/upgrade/devices", tags=["upgrade"])


class DeletableImageOut(BaseModel):
    version: str
    size_kb: str | None
    release_type: str


class DiskCleanupPlanOut(BaseModel):
    device_id: int
    device_name: str
    current_version: str | None
    # df-style rows: [{filesystem, size, used, avail, use_pct, mounted_on}].
    disk_space: list[dict]
    # Downloaded images outside the current feature train — the only things
    # the cleanup will delete. Empty when the current train can't be parsed
    # (we refuse to guess) or nothing is reclaimable.
    deletable_images: list[DeletableImageOut]


class DiskCleanupExecuteIn(BaseModel):
    # Versions the operator approved from the plan. Re-validated server-side
    # against the recomputed safe set — anything not in it is refused.
    versions: list[str]


class DiskCleanupResultOut(BaseModel):
    device_id: int
    device_name: str
    deleted: list[str]
    failed: list[dict]
    standard_cleanup_ran: bool
    standard_cleanup_output: str
    disk_space_before: list[dict]
    disk_space_after: list[dict]


def _device_or_404(db: Session, device_id: int) -> Device:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")
    return device


@router.get("/{device_id}/disk-cleanup/plan", response_model=DiskCleanupPlanOut)
def get_disk_cleanup_plan(
    device_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> DiskCleanupPlanOut:
    """Dry-run: current disk usage + the old-train images we'd delete.

    Pure reads — nothing is deleted. The UI shows this so the operator can
    confirm before any destructive call. Slow-ish (queries the device's
    software catalog + disk-space through the proxy).
    """
    device = _device_or_404(db, device_id)
    try:
        plan = svc.plan_cleanup(db, device)
    except Exception as exc:  # noqa: BLE001 — surface as a clean 502
        raise HTTPException(
            status_code=502, detail=f"could not build cleanup plan: {str(exc)[:300]}"
        ) from exc
    return DiskCleanupPlanOut(
        device_id=plan.device_id,
        device_name=plan.device_name,
        current_version=plan.current_version,
        disk_space=plan.disk_space,
        deletable_images=[
            DeletableImageOut(
                version=i.version, size_kb=i.size_kb, release_type=i.release_type
            )
            for i in plan.deletable_images
        ],
    )


@router.post("/{device_id}/disk-cleanup", response_model=DiskCleanupResultOut)
def run_disk_cleanup(
    device_id: int,
    payload: DiskCleanupExecuteIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> DiskCleanupResultOut:
    """Delete the approved (and server-re-validated) old-train images, then
    run the standard disk-usage cleanup. Returns disk-space before/after and
    a per-image outcome."""
    device = _device_or_404(db, device_id)
    if not payload.versions:
        raise HTTPException(status_code=400, detail="versions must be non-empty")
    try:
        result = svc.execute_cleanup(db, device, payload.versions)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"disk cleanup failed: {str(exc)[:300]}"
        ) from exc
    return DiskCleanupResultOut(
        device_id=result.device_id,
        device_name=result.device_name,
        deleted=result.deleted,
        failed=result.failed,
        standard_cleanup_ran=result.standard_cleanup_ran,
        standard_cleanup_output=result.standard_cleanup_output,
        disk_space_before=result.disk_space_before,
        disk_space_after=result.disk_space_after,
    )
