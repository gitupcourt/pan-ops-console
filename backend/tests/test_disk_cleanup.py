"""Disk-cleanup safe-set logic + the execute guard (conservative scope).

The cleanup only ever deletes downloaded software images OUTSIDE the device's
current feature train; the current train (incl. its base, which a within-train
rollback may need) and the running version are never touched. `execute_cleanup`
re-validates the requested versions server-side, so a tampered request can't
widen the blast radius.
"""

from __future__ import annotations

from app.core.devices.models.device import Device
from app.core.devices.models.enums import DeviceSource, HARole
from app.upgrade.services import disk_cleanup as svc


def _device(db, name="fw1", current="11.1.4") -> Device:
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
    d.current_version = current
    db.commit()
    db.refresh(d)
    return d


# Running 11.1.4. Downloaded on disk: 11.1.4 (current — KEEP), 11.1.2 (the
# current train's base — KEEP), 10.2.0 + 11.0.3 (old trains — DELETE).
# 12.1.2 is a newer base but NOT downloaded, so it isn't a candidate.
SOFTWARE = [
    {"version": "11.1.4", "downloaded": True, "current": True, "release_type": "", "size_kb": "500000"},
    {"version": "11.1.2", "downloaded": True, "current": False, "release_type": "Base", "size_kb": "700000"},
    {"version": "10.2.0", "downloaded": True, "current": False, "release_type": "Base", "size_kb": "650000"},
    {"version": "11.0.3", "downloaded": True, "current": False, "release_type": "", "size_kb": "300000"},
    {"version": "12.1.2", "downloaded": False, "current": False, "release_type": "Base", "size_kb": "800000"},
]


# ---------- _deletable_images (pure) ----------

def test_deletable_images_only_old_train_downloaded():
    out = svc._deletable_images(SOFTWARE, "11.1.4")
    assert sorted(i.version for i in out) == ["10.2.0", "11.0.3"]


def test_deletable_images_excludes_current_train_base():
    # 11.1.2 is downloaded + not current, but it's the CURRENT train's base —
    # must never be offered for deletion.
    out = svc._deletable_images(SOFTWARE, "11.1.4")
    assert "11.1.2" not in {i.version for i in out}


def test_deletable_images_empty_when_current_unparseable():
    # Can't determine the current train -> refuse to guess -> delete nothing.
    assert svc._deletable_images(SOFTWARE, None) == []
    assert svc._deletable_images(SOFTWARE, "garbage") == []


# ---------- execute_cleanup (server-side safety re-validation) ----------

class _FakeClient:
    def __init__(self):
        self.deleted: list[str] = []
        self.cleanup_called = False
        self._disk = [{
            "filesystem": "/dev/root", "size": "7.0G", "used": "6.0G",
            "avail": "1.0G", "use_pct": "86", "mounted_on": "/",
        }]

    def list_software(self):
        return SOFTWARE

    def get_disk_space(self):
        return self._disk

    def delete_software_image(self, v):
        self.deleted.append(v)

    def disk_usage_cleanup(self):
        self.cleanup_called = True
        return "Cleanup complete"


def test_execute_refuses_versions_outside_safe_set(client, db, monkeypatch):
    dev = _device(db, current="11.1.4")
    fake = _FakeClient()
    monkeypatch.setattr(
        svc, "build_client_with_fallback", lambda db, device: (fake, "direct")
    )

    # Request a safe old-train image, the RUNNING version, and the current
    # train's base. Only the first may be deleted.
    result = svc.execute_cleanup(db, dev, ["10.2.0", "11.1.4", "11.1.2"])

    assert fake.deleted == ["10.2.0"]
    assert {f["version"] for f in result.failed} == {"11.1.4", "11.1.2"}
    assert result.standard_cleanup_ran is True
    assert fake.cleanup_called is True
    assert result.disk_space_before and result.disk_space_after


def test_execute_standard_cleanup_is_nonfatal(client, db, monkeypatch):
    dev = _device(db, current="11.1.4")
    fake = _FakeClient()

    def _boom():
        raise ConnectionError("disk-usage cleanup failed: unknown command")

    fake.disk_usage_cleanup = _boom
    monkeypatch.setattr(
        svc, "build_client_with_fallback", lambda db, device: (fake, "direct")
    )

    # The image delete is the primary win; a cleanup-command rejection must
    # not fail the whole operation.
    result = svc.execute_cleanup(db, dev, ["10.2.0"])
    assert fake.deleted == ["10.2.0"]
    assert result.standard_cleanup_ran is False
    assert "unknown command" in result.standard_cleanup_output
