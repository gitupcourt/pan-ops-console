"""HTTP surface for reading captured snapshots and their diffs.

Scope of this PR:
- GET /upgrade/snapshots/{id} → full snapshot row (incl. `data` blob)
- GET /upgrade/snapshot-diffs/{id} → full diff row (incl. `report` blob)

The orchestrator already captures pre/post snapshots and computes diffs
during upgrade jobs; this surface lets the JobDetail UI render them
("here's what changed after the upgrade").

Out of scope (follow-up): list-by-device, ad-hoc capture, retention
rules, cross-device drift comparison. The IDs needed for the
JobDetail flow are surfaced on `TaskRead.pre_snapshot_id /
post_snapshot_id / snapshot_diff_id` (see schemas.py); these endpoints
just resolve those IDs to full payloads when the operator clicks
"View" or "Compare."
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.auth.deps import current_user
from app.core.auth.models.user import User
from app.db import get_db
from app.upgrade.models.snapshot import Snapshot, SnapshotDiff
from app.upgrade.schemas import SnapshotDiffRead, SnapshotRead

router = APIRouter(tags=["upgrade"])


def _device_display_name(snap: Snapshot) -> str:
    d = snap.device
    return d.name or d.hostname or f"device-{d.id}"


def _snapshot_to_read(snap: Snapshot) -> SnapshotRead:
    return SnapshotRead.model_validate(
        {
            "id": snap.id,
            "device_id": snap.device_id,
            "device_name": _device_display_name(snap),
            "task_id": snap.task_id,
            "kind": snap.kind,
            "taken_at": snap.taken_at,
            "pan_os_version": snap.pan_os_version,
            "error": snap.error,
            "data": snap.data,
        }
    )


def _diff_to_read(diff: SnapshotDiff) -> SnapshotDiffRead:
    return SnapshotDiffRead.model_validate(
        {
            "id": diff.id,
            "left_snapshot_id": diff.left_snapshot_id,
            "right_snapshot_id": diff.right_snapshot_id,
            "task_id": diff.task_id,
            "computed_at": diff.computed_at,
            "all_passed": diff.all_passed,
            "failing_areas": diff.failing_areas,
            "report": diff.report,
            "left_version": diff.left.pan_os_version if diff.left else None,
            "right_version": diff.right.pan_os_version if diff.right else None,
        }
    )


@router.get("/upgrade/snapshots/{snapshot_id}", response_model=SnapshotRead)
def get_snapshot(
    snapshot_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Fetch a full snapshot by id, including the per-area `data` blob.

    The blob can be sizable (hundreds of KB) — the UI is expected to
    render it lazily / per-area. Auth-gated; no operator role check
    beyond authenticated session (snapshots don't carry secrets).
    """
    snap = db.get(Snapshot, snapshot_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return _snapshot_to_read(snap)


@router.get(
    "/upgrade/snapshot-diffs/{diff_id}", response_model=SnapshotDiffRead
)
def get_snapshot_diff(
    diff_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Fetch a full diff by id, including the per-area report.

    The report shape is `{area: {passed, added, missing, changed}}`
    where added/missing are lists of identifying tuples and changed
    is a per-row before/after map. The UI is expected to render each
    area collapsibly so a "1 changed route" diff doesn't drown out a
    "23 changed sessions" diff.
    """
    diff = db.get(SnapshotDiff, diff_id)
    if diff is None:
        raise HTTPException(status_code=404, detail="snapshot diff not found")
    return _diff_to_read(diff)
