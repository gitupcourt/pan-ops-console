"""Tests for /upgrade/snapshots/{id} and /upgrade/snapshot-diffs/{id}.

Surface contract:
  - GET /upgrade/snapshots/{id} returns the full snapshot incl. `data`
  - GET /upgrade/snapshot-diffs/{id} returns the full diff incl. `report`
  - Both 404 on unknown IDs
  - Both 401 without an auth session

The snapshots themselves are produced by the orchestrator inside a
real upgrade run; for these tests we just seed rows directly via the
DB fixture so we're testing the HTTP surface in isolation, not
panos-upgrade-assurance's snapshot runner.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.devices.models.device import Device
from app.core.devices.models.enums import DeviceSource, HARole
from app.upgrade.models.snapshot import Snapshot, SnapshotDiff, SnapshotKind


STRONG = "correct horse battery staple"


def _signup(client):
    return client.post(
        "/auth/signup-first",
        json={"username": "admin", "password": STRONG},
    )


def _seed_device(db, name="fw1") -> Device:
    d = Device(
        name=name,
        hostname=f"{name}.local",
        verify_tls=False,
        proxy_via_panorama=False,
        polling_enabled=True,
        source=DeviceSource.DIRECT,
        ha_role=HARole.STANDALONE,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def _seed_snapshot(db, device, *, kind=SnapshotKind.PRE_UPGRADE, version="10.2.0") -> Snapshot:
    snap = Snapshot(
        device_id=device.id,
        task_id=None,
        kind=kind,
        data={"routes": {"rows": [{"dest": "0.0.0.0/0"}]}, "license": {"valid": True}},
        pan_os_version=version,
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


def test_get_snapshot_returns_full_payload(client, db):
    _signup(client)
    dev = _seed_device(db)
    snap = _seed_snapshot(db, dev, kind=SnapshotKind.POST_UPGRADE, version="11.2.7-h15")

    r = client.get(f"/upgrade/snapshots/{snap.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == snap.id
    assert body["device_id"] == dev.id
    assert body["device_name"] == dev.name
    assert body["kind"] == "post_upgrade"
    assert body["pan_os_version"] == "11.2.7-h15"
    # Per-area data is exposed verbatim.
    assert body["data"]["routes"]["rows"][0]["dest"] == "0.0.0.0/0"
    assert body["data"]["license"]["valid"] is True


def test_get_unknown_snapshot_404s(client):
    _signup(client)
    r = client.get("/upgrade/snapshots/99999")
    assert r.status_code == 404


def test_get_snapshot_diff_returns_report_and_versions(client, db):
    """Diff payload includes left/right snapshot IDs + their PAN-OS
    versions (denormalized so the UI header can show "10.2.0 → 11.2.7-h15"
    without re-fetching the snapshot rows)."""
    _signup(client)
    dev = _seed_device(db)
    pre = _seed_snapshot(db, dev, kind=SnapshotKind.PRE_UPGRADE, version="10.2.0")
    post = _seed_snapshot(db, dev, kind=SnapshotKind.POST_UPGRADE, version="11.2.7-h15")

    diff = SnapshotDiff(
        left_snapshot_id=pre.id,
        right_snapshot_id=post.id,
        task_id=None,
        report={
            "routes": {"passed": False, "added": [], "missing": [["1.2.3.0/24"]], "changed": {}},
            "license": {"passed": True, "added": [], "missing": [], "changed": {}},
        },
        all_passed=False,
        failing_areas="routes",
    )
    db.add(diff)
    db.commit()
    db.refresh(diff)

    r = client.get(f"/upgrade/snapshot-diffs/{diff.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == diff.id
    assert body["left_snapshot_id"] == pre.id
    assert body["right_snapshot_id"] == post.id
    assert body["all_passed"] is False
    assert body["failing_areas"] == "routes"
    assert body["left_version"] == "10.2.0"
    assert body["right_version"] == "11.2.7-h15"
    # Full report is exposed verbatim.
    assert body["report"]["routes"]["passed"] is False
    assert body["report"]["license"]["passed"] is True


def test_get_unknown_snapshot_diff_404s(client):
    _signup(client)
    r = client.get("/upgrade/snapshot-diffs/99999")
    assert r.status_code == 404


def test_snapshot_endpoints_require_auth(client):
    # No signup → 401.
    assert client.get("/upgrade/snapshots/1").status_code == 401
    assert client.get("/upgrade/snapshot-diffs/1").status_code == 401
