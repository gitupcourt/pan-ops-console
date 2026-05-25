"""Snapshot endpoints: list per device, fetch one, compute ad-hoc diff.

The orchestrator writes Snapshots/SnapshotDiffs as part of the upgrade flow;
these endpoints expose them to the UI so the operator can review the
config/runtime delta after an upgrade.

There's intentionally no "POST /api/snapshots" to take an ad-hoc snapshot
from the UI yet — capture means a live PAN-OS round-trip and we want a
dedicated worker task for that so it doesn't tie up an API thread. Stub
endpoint at the bottom returns 501 so the path is visible in the OpenAPI.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.db import get_db
from app.models.device import Device
from app.models.snapshot import Snapshot, SnapshotDiff, SnapshotKind
from app.models.user import User
from app.services import snapshot as snapshot_svc

router = APIRouter(prefix="/api/snapshots", tags=["snapshots"])
device_router = APIRouter(prefix="/api/devices", tags=["snapshots"])


# ---------- response models ----------


class SnapshotSummaryOut(BaseModel):
    """Slim row for list endpoints — no `data` blob (can be tens of KB)."""

    id: int
    device_id: int
    task_id: int | None
    kind: str
    taken_at: str
    pan_os_version: str | None
    error: str | None
    areas: list[str]

    model_config = {"from_attributes": True}

    @classmethod
    def from_row(cls, s: Snapshot) -> "SnapshotSummaryOut":
        return cls(
            id=s.id,
            device_id=s.device_id,
            task_id=s.task_id,
            kind=s.kind.value,
            taken_at=s.taken_at.isoformat() if s.taken_at else "",
            pan_os_version=s.pan_os_version,
            error=s.error,
            areas=sorted(s.data.keys()) if s.data else [],
        )


class SnapshotOut(SnapshotSummaryOut):
    """Full snapshot including the data blob."""

    data: dict

    @classmethod
    def from_row(cls, s: Snapshot) -> "SnapshotOut":  # type: ignore[override]
        base = SnapshotSummaryOut.from_row(s)
        return cls(**base.model_dump(), data=s.data or {})


class SnapshotDiffOut(BaseModel):
    id: int
    left_snapshot_id: int
    right_snapshot_id: int
    task_id: int | None
    computed_at: str
    all_passed: bool
    failing_areas: str | None
    report: dict
    left: SnapshotSummaryOut
    right: SnapshotSummaryOut

    @classmethod
    def from_row(cls, d: SnapshotDiff) -> "SnapshotDiffOut":
        return cls(
            id=d.id,
            left_snapshot_id=d.left_snapshot_id,
            right_snapshot_id=d.right_snapshot_id,
            task_id=d.task_id,
            computed_at=d.computed_at.isoformat() if d.computed_at else "",
            all_passed=d.all_passed,
            failing_areas=d.failing_areas,
            report=d.report or {},
            left=SnapshotSummaryOut.from_row(d.left),
            right=SnapshotSummaryOut.from_row(d.right),
        )


# ---------- per-device list ----------


@device_router.get("/{device_id}/snapshots", response_model=list[SnapshotSummaryOut])
def list_device_snapshots(
    device_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    if db.get(Device, device_id) is None:
        raise HTTPException(404, "Device not found")
    rows = (
        db.query(Snapshot)
        .filter(Snapshot.device_id == device_id)
        .order_by(desc(Snapshot.taken_at))
        .limit(min(limit, 200))
        .all()
    )
    return [SnapshotSummaryOut.from_row(r) for r in rows]


# ---------- single snapshot ----------


@router.get("/{snapshot_id}", response_model=SnapshotOut)
def get_snapshot(
    snapshot_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    snap = db.get(Snapshot, snapshot_id)
    if snap is None:
        raise HTTPException(404, "Snapshot not found")
    return SnapshotOut.from_row(snap)


# ---------- diff lookup ----------


@router.get("/diffs/{diff_id}", response_model=SnapshotDiffOut)
def get_diff(
    diff_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    diff = db.get(SnapshotDiff, diff_id)
    if diff is None:
        raise HTTPException(404, "Snapshot diff not found")
    return SnapshotDiffOut.from_row(diff)


@router.get("/diffs/by-task/{task_id}", response_model=SnapshotDiffOut | None)
def get_diff_for_task(
    task_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Any:
    """The canonical pre→post diff produced by the upgrade orchestrator for
    this task, if any. Returns null (200) when the diff doesn't exist yet —
    the upgrade may still be in flight, or the snapshot capture failed."""
    diff = (
        db.query(SnapshotDiff)
        .filter(SnapshotDiff.task_id == task_id)
        .order_by(desc(SnapshotDiff.computed_at))
        .first()
    )
    if diff is None:
        return None
    return SnapshotDiffOut.from_row(diff)


# ---------- ad-hoc compare (any two snapshots) ----------


@router.post("/diffs/compare", response_model=SnapshotDiffOut)
def compare_two(
    left_id: int,
    right_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Compute (or fetch the cached) diff between any two persisted snapshots.

    Idempotent — if a diff already exists for this (left, right) pair we
    return it rather than re-computing."""
    left = db.get(Snapshot, left_id)
    right = db.get(Snapshot, right_id)
    if left is None or right is None:
        raise HTTPException(404, "One or both snapshots not found")

    existing = (
        db.query(SnapshotDiff)
        .filter(
            SnapshotDiff.left_snapshot_id == left_id,
            SnapshotDiff.right_snapshot_id == right_id,
        )
        .first()
    )
    if existing is not None:
        return SnapshotDiffOut.from_row(existing)

    diff = snapshot_svc.compare(db, left, right)
    if diff is None:
        raise HTTPException(
            422,
            "Cannot compare: one or both snapshots have no data (capture likely failed)",
        )
    return SnapshotDiffOut.from_row(diff)


# ---------- ad-hoc capture (deferred) ----------


@device_router.post("/{device_id}/snapshots")
def take_ad_hoc_snapshot(
    device_id: int,
    _db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Reserved for a future "Take snapshot now" button. Capture is a slow
    PAN-OS round-trip, so this should be a Celery task — not a sync endpoint."""
    raise HTTPException(501, "Ad-hoc snapshot capture not yet implemented")
