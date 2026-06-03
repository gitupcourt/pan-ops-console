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


# Running 11.1.4. Downloaded on disk:
#   11.1.4  current                       -> KEEP (running)
#   11.1.2  same train, Base, older       -> KEEP (current train's base)
#   11.1.3  same train, older maintenance -> DELETE (the same-train win)
#   11.1.7  same train, NEWER (pre-staged)-> KEEP (don't undo staging)
#   10.2.0  Base, older other train       -> DELETE
#   11.0.3  older other train             -> DELETE
#   12.1.2  newer base, NOT downloaded     -> skip (not on disk)
SOFTWARE = [
    {"version": "11.1.4", "downloaded": True, "current": True, "release_type": "", "size_kb": "500000"},
    {"version": "11.1.2", "downloaded": True, "current": False, "release_type": "Base", "size_kb": "700000"},
    {"version": "11.1.3", "downloaded": True, "current": False, "release_type": "", "size_kb": "520000"},
    {"version": "11.1.7", "downloaded": True, "current": False, "release_type": "", "size_kb": "540000"},
    {"version": "10.2.0", "downloaded": True, "current": False, "release_type": "Base", "size_kb": "650000"},
    {"version": "11.0.3", "downloaded": True, "current": False, "release_type": "", "size_kb": "300000"},
    {"version": "12.1.2", "downloaded": False, "current": False, "release_type": "Base", "size_kb": "800000"},
]


# ---------- _deletable_images (pure) ----------

def test_deletable_images_old_other_and_same_train():
    # Old other-train images AND old same-train maintenance releases, but NOT
    # the current train's base (11.1.2), the running version (11.1.4), or a
    # newer pre-staged image (11.1.7), or an un-downloaded one (12.1.2).
    out = svc._deletable_images(SOFTWARE, "11.1.4")
    assert sorted(i.version for i in out) == ["10.2.0", "11.0.3", "11.1.3"]


def test_deletable_images_excludes_current_train_base():
    # 11.1.2 is downloaded + not current + older, but it's the CURRENT train's
    # base — must never be offered for deletion.
    out = svc._deletable_images(SOFTWARE, "11.1.4")
    assert "11.1.2" not in {i.version for i in out}


def test_deletable_images_keeps_newer_staged():
    # 11.1.7 is a downloaded same-train image NEWER than the running 11.1.4 —
    # almost certainly pre-staged for an upcoming upgrade. Never auto-delete it.
    out = svc._deletable_images(SOFTWARE, "11.1.4")
    assert "11.1.7" not in {i.version for i in out}


def test_deletable_images_empty_when_current_unparseable():
    # Can't determine the running version -> refuse to guess -> delete nothing.
    assert svc._deletable_images(SOFTWARE, None) == []
    assert svc._deletable_images(SOFTWARE, "garbage") == []


# ---------- execute_cleanup (server-side safety re-validation) ----------

class _FakeClient:
    def __init__(self):
        self.deleted: list[str] = []
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


def test_execute_refuses_versions_outside_safe_set(client, db, monkeypatch):
    dev = _device(db, current="11.1.4")
    fake = _FakeClient()
    monkeypatch.setattr(
        svc, "build_client_with_fallback", lambda db, device: (fake, "direct")
    )

    # Request a safe old image, the RUNNING version, and the current train's
    # base. Only the first may be deleted.
    result = svc.execute_cleanup(db, dev, ["10.2.0", "11.1.4", "11.1.2"])

    assert fake.deleted == ["10.2.0"]
    assert {f["version"] for f in result.failed} == {"11.1.4", "11.1.2"}
    assert result.disk_space_before and result.disk_space_after
