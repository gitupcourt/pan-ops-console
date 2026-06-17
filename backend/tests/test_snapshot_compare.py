"""Tests for the snapshot compare service.

Regression focus: `compare()` must only ask panos-upgrade-assurance to
compare the areas actually present in BOTH snapshots. Calling
`compare_snapshots()` with no `reports=` makes the library expand to its
FULL built-in report list (which includes areas we never capture, e.g.
`global_jumbo_frame`), and it then raises MissingKeyException — which we
were silently persisting as a `{"_error": ...}` diff. That bug shipped a
useless "1 of 1 areas changed: _error" panel after the first real
upgrade (Job #3).

We assert the fix at the seam we control (the `reports=` argument we
pass to the library) rather than against the library's per-area
comparison internals — `session_stats` and friends have their own
threshold/shape rules that would make hand-built fixtures fragile.
"""

from __future__ import annotations

from app.core.devices.models.device import Device
from app.core.devices.models.enums import DeviceSource, HARole
from app.upgrade.models.snapshot import Snapshot, SnapshotDiff, SnapshotKind
from app.upgrade.services import snapshot as snapshot_svc


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


def _snap(db, device, kind, data, version="11.2.0") -> Snapshot:
    s = Snapshot(
        device_id=device.id,
        task_id=None,
        kind=kind,
        data=data,
        pan_os_version=version,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


class _FakeCompare:
    """Stand-in for panos_upgrade_assurance.SnapshotCompare that records
    the `reports=` it was asked to compare and returns an all-passed
    report for exactly those areas."""

    last_reports: list | None = None

    def __init__(self, left_data, right_data):
        self.left_data = left_data
        self.right_data = right_data

    def compare_snapshots(self, reports=None):
        _FakeCompare.last_reports = reports
        # Mimic the library: if asked for an area not in both snapshots,
        # it would raise. Our fix guarantees `reports` only contains
        # overlapping areas, so build a clean per-area report.
        return {area: {"passed": True} for area in (reports or [])}


class _PartialFakeCompare:
    """SnapshotCompare stand-in that RAISES for one area — mimics e.g. `routes`
    on an Advanced-Routing-Mode device, whose capture errors to a null snapshot
    so the real library raises 'Cannot compare snapshot when either side is
    None'. All other areas compare clean."""

    def __init__(self, left_data, right_data):
        pass

    def compare_snapshots(self, reports=None):
        (area,) = reports  # compare() now calls one area at a time
        if area == "routes":
            raise ValueError("Cannot compare snapshot when either side is None")
        return {area: {"passed": True}}


def test_compare_skips_uncomparable_area(client, db, monkeypatch):
    """A single area the library can't compare (routes in Advanced Routing Mode)
    must NOT sink the whole diff. compare() goes area-by-area, skips the bad one,
    reports the rest, and records the skip under `_skipped`."""
    monkeypatch.setattr(snapshot_svc, "SnapshotCompare", _PartialFakeCompare)

    dev = _seed_device(db, name="fw-arm")
    data = {"nics": {}, "routes": {}, "arp_table": {}, "license": {}}
    pre = _snap(db, dev, SnapshotKind.PRE_UPGRADE, data)
    post = _snap(db, dev, SnapshotKind.POST_UPGRADE, data, version="12.1.4-h2")

    diff = snapshot_svc.compare(db, pre, post)

    assert diff is not None
    assert "_error" not in diff.report                 # the diff SURVIVED
    assert diff.report.get("_skipped") == ["routes"]   # the bad area is recorded
    assert {"nics", "arp_table", "license"} <= set(diff.report)  # the rest compared
    assert diff.all_passed is True


def test_compare_passes_only_overlapping_areas(client, db, monkeypatch):
    """The core regression: compare() must scope the library call to the
    areas present in BOTH snapshots — never None (which would pull in
    global_jumbo_frame etc. and blow up)."""
    monkeypatch.setattr(snapshot_svc, "SnapshotCompare", _FakeCompare)

    dev = _seed_device(db)
    left_data = {
        "nics": {},
        "routes": {},
        "license": {},
        "arp_table": {},
        "content_version": {},
        "ip_sec_tunnels": {},
        "session_stats": {},
    }
    # Right side is missing ip_sec_tunnels + has an extra (uncaptured) area.
    right_data = {
        "nics": {},
        "routes": {},
        "license": {},
        "arp_table": {},
        "content_version": {},
        "session_stats": {},
        "global_jumbo_frame": {},  # present on one side only — must be excluded
    }
    pre = _snap(db, dev, SnapshotKind.PRE_UPGRADE, left_data)
    post = _snap(db, dev, SnapshotKind.POST_UPGRADE, right_data, version="11.2.7-h15")

    diff = snapshot_svc.compare(db, pre, post)

    assert diff is not None
    # Never None — that's the whole bug.
    assert _FakeCompare.last_reports is not None
    # Exactly the intersection, sorted, and crucially WITHOUT
    # global_jumbo_frame or ip_sec_tunnels (each present on one side only)
    # and WITHOUT session_stats (in COMPARE_EXCLUDE_AREAS — see the
    # dedicated test below).
    assert _FakeCompare.last_reports == [
        "arp_table",
        "content_version",
        "license",
        "nics",
        "routes",
    ]
    assert "_error" not in diff.report
    assert diff.all_passed is True


def test_compare_excludes_uncomparable_areas(client, db, monkeypatch):
    """`session_stats` is captured for raw visibility but must never be
    handed to the library's compare: a bare compare (no threshold spec)
    returns a null per-area report, which is meaningless across the upgrade
    reboot (counters reset) AND was the null payload that crashed the
    Compare-diff viewer. Present in both snapshots, still excluded."""
    monkeypatch.setattr(snapshot_svc, "SnapshotCompare", _FakeCompare)

    dev = _seed_device(db)
    data = {"nics": {}, "routes": {}, "session_stats": {}}
    pre = _snap(db, dev, SnapshotKind.PRE_UPGRADE, data)
    post = _snap(db, dev, SnapshotKind.POST_UPGRADE, data, version="12.1.4-h6")

    diff = snapshot_svc.compare(db, pre, post)

    assert diff is not None
    assert _FakeCompare.last_reports == ["nics", "routes"]
    # session_stats never reaches the library and never lands in the report.
    assert "session_stats" not in diff.report


def test_compare_skips_when_no_overlapping_areas(client, db, monkeypatch):
    """Defensive: if the two snapshots share no captured areas, skip
    rather than hand the library an empty report list (which would also
    expand to the full default set and raise)."""
    monkeypatch.setattr(snapshot_svc, "SnapshotCompare", _FakeCompare)

    dev = _seed_device(db)
    pre = _snap(db, dev, SnapshotKind.PRE_UPGRADE, {"nics": {}})
    post = _snap(db, dev, SnapshotKind.POST_UPGRADE, {"routes": {}})

    diff = snapshot_svc.compare(db, pre, post)
    assert diff is None


def test_compare_skips_when_one_side_empty(client, db, monkeypatch):
    """A diff against a failed-capture snapshot (empty data) is
    meaningless — skip it. Pre-existing behavior, pinned here so the
    area-intersection change doesn't regress it."""
    monkeypatch.setattr(snapshot_svc, "SnapshotCompare", _FakeCompare)

    dev = _seed_device(db)
    pre = _snap(db, dev, SnapshotKind.PRE_UPGRADE, {})  # capture failed
    post = _snap(db, dev, SnapshotKind.POST_UPGRADE, {"nics": {}})

    diff = snapshot_svc.compare(db, pre, post)
    assert diff is None


def test_compare_persists_error_when_library_raises(client, db, monkeypatch):
    """If the library DOES raise (some other reason), we still persist a
    diff row with the error so the UI doesn't silently miss it — but this
    should no longer fire for the global_jumbo_frame case."""

    class _RaisingCompare:
        def __init__(self, *a):
            pass

        def compare_snapshots(self, reports=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(snapshot_svc, "SnapshotCompare", _RaisingCompare)

    dev = _seed_device(db)
    pre = _snap(db, dev, SnapshotKind.PRE_UPGRADE, {"nics": {}})
    post = _snap(db, dev, SnapshotKind.POST_UPGRADE, {"nics": {}})

    diff = snapshot_svc.compare(db, pre, post)
    assert diff is not None
    assert diff.report.get("_error") == "boom"
    assert diff.all_passed is False
    assert isinstance(db.get(SnapshotDiff, diff.id), SnapshotDiff)
