"""Snapshot capture + compare service.

Thin wrapper over pan-os-upgrade-assurance's `run_snapshots()` /
`SnapshotCompare` that persists results into our DB so the UI can render
diffs long after the worker that produced them is gone.

The orchestrator calls into this twice per upgrade task:
  - `capture(...PRE_UPGRADE...)` before install
  - `capture(...POST_UPGRADE...)` after wait_for_ready returns
  - `compare(pre, post, task_id=...)` once both exist

A failed capture is intentionally non-fatal — we write a row with empty
`data` and an `error` message so the timeline reflects what happened, but
we don't abort the upgrade for it.

Picking snapshot areas: the library's default list is good but heavy.
`DEFAULT_AREAS` here is the subset we always want, chosen because they
catch the regressions an upgrade typically introduces (route table
shifts, session counts collapse, NIC counters reset, license state).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from panos_upgrade_assurance.snapshot_compare import SnapshotCompare
from sqlalchemy.orm import Session

from app.upgrade.models.snapshot import Snapshot, SnapshotDiff, SnapshotKind

if TYPE_CHECKING:
    from app.core.command_proxy.pan_client import PanDeviceClient
    from app.core.devices.models.device import Device

log = logging.getLogger(__name__)


# Snapshot areas we capture by default. Order doesn't matter for compare.
# Keep this conservative — every extra area is XML round-trip latency
# during a maintenance window, and `sessions` in particular can be massive
# on a busy firewall. Operators can run an ad-hoc snapshot with a custom
# list via the API if they want more.
DEFAULT_AREAS: list[str] = [
    "nics",
    "routes",
    "license",
    "arp_table",
    "content_version",
    "ip_sec_tunnels",
    "session_stats",
]


def capture(
    db: Session,
    device: "Device",
    client: "PanDeviceClient",
    kind: SnapshotKind,
    *,
    task_id: int | None = None,
    areas: list[str] | None = None,
) -> Snapshot:
    """Take a snapshot and persist it. Always returns a Snapshot row.

    On capture failure we still write a row (with empty data + error message)
    so the orchestrator timeline can reference it and a future "View
    snapshots" UI shows the attempt. The caller decides whether to keep
    going.
    """
    areas = areas or DEFAULT_AREAS

    try:
        data = client.take_snapshot(snapshots=areas)
        error: str | None = None
    except Exception as exc:  # noqa: BLE001
        log.warning("Snapshot capture failed for %s: %s", device.name, exc)
        data = {}
        error = str(exc)[:1900]

    snap = Snapshot(
        device_id=device.id,
        task_id=task_id,
        kind=kind,
        data=data,
        pan_os_version=device.current_version,
        error=error,
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def compare(
    db: Session,
    left: Snapshot,
    right: Snapshot,
    *,
    task_id: int | None = None,
) -> SnapshotDiff | None:
    """Compare two snapshots, persist + return the SnapshotDiff row.

    Returns None (and writes nothing) when either side has empty data —
    a comparison against a failed-capture snapshot would be meaningless.
    """
    if not left.data or not right.data:
        log.info(
            "Skipping snapshot diff (left=%s right=%s): one side has no data",
            left.id, right.id,
        )
        return None

    try:
        # SnapshotCompare needs the two dicts; `reports=None` means "every
        # area present in both snapshots, default comparison rules."
        report = SnapshotCompare(left.data, right.data).compare_snapshots()
    except Exception as exc:  # noqa: BLE001
        log.warning("Snapshot compare failed: %s", exc)
        # Persist the failure so the UI doesn't silently miss the diff.
        diff = SnapshotDiff(
            left_snapshot_id=left.id,
            right_snapshot_id=right.id,
            task_id=task_id,
            report={"_error": str(exc)[:1900]},
            all_passed=False,
            failing_areas="(compare error)",
        )
        db.add(diff)
        db.commit()
        db.refresh(diff)
        return diff

    # The library report shape: {area: {"passed": bool, ...}}. Aggregate the
    # pass flag so list endpoints don't have to descend into the JSON.
    failing = [
        name for name, body in report.items()
        if isinstance(body, dict) and body.get("passed") is False
    ]
    diff = SnapshotDiff(
        left_snapshot_id=left.id,
        right_snapshot_id=right.id,
        task_id=task_id,
        report=report,
        all_passed=len(failing) == 0,
        failing_areas=", ".join(sorted(failing)) or None,
    )
    db.add(diff)
    db.commit()
    db.refresh(diff)
    return diff
