"""`_phase_ensure_image` base-image detection — regression guard for a major-jump
staging failure where every device failed with "download did not land after N
attempts".

Root cause: PR #175 made `PanDeviceClient.list_software()` local-only by default
(check_updates=False) to speed up the version picker. But `_phase_ensure_image`
feeds that list to `base_image_from_list()` to find the target train's BASE
image — which a FRESH major jump (11.2.x -> 12.1.x) hasn't downloaded yet, so it
isn't in the local list and base detection returned None. The base download was
then silently skipped and the maintenance release downloaded but never "landed"
(PAN-OS won't surface a maintenance release until its train's base is present).

Fix: when the local list yields no base, re-query WITH the update-server list
(check_updates=True). Local-first keeps the common case (base already present)
cheap and avoids an update-server round-trip on every poll re-entry.

Same no-DB SimpleNamespace + MagicMock + monkeypatch style as the other
test_upgrade_*.py modules (backend is Python 3.11).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.upgrade.models.enums import TaskPhase
from app.upgrade.services import upgrade
from app.upgrade.services.upgrade import _Wait

# A device on 11.2.7-h15 targeting 12.1.4-h2 (a major jump). Its LOCAL software
# list (check_updates=False) carries only 11.2.x images — no 12.1 train at all,
# so base_image_from_list() can't find the base there.
LOCAL_NO_TARGET_TRAIN = [
    {"version": "11.2.7-h15", "release_type": "", "downloaded": True, "current": True},
    {"version": "11.2.0", "release_type": "Base", "downloaded": True, "current": False},
]
# The update-server list (check_updates=True) additionally carries the 12.1
# train, whose base is 12.1.2 (NOT 12.1.0).
AVAILABLE_WITH_BASE = [
    {"version": "12.1.4-h2", "release_type": "", "downloaded": False, "current": False},
    {"version": "12.1.2", "release_type": "Base", "downloaded": False, "current": False},
    *LOCAL_NO_TARGET_TRAIN,
]
# Base already downloaded locally — the local list shows the 12.1 train, so base
# detection succeeds without an update-server round-trip.
LOCAL_WITH_BASE = [
    {"version": "12.1.2", "release_type": "Base", "downloaded": True, "current": False},
    *LOCAL_NO_TARGET_TRAIN,
]


class _SoftwareListClient:
    """Records each list_software() call's check_updates flag so a test can prove
    whether the update-server fallback fired. is_version_downloaded() reads the
    LOCAL list directly (doesn't go through list_software) so it never pollutes
    the recorded call sequence."""

    def __init__(self, *, local, available):
        self._local = local
        self._available = available
        self.list_calls: list[bool] = []

    def list_software(self, check_updates: bool = False):
        self.list_calls.append(check_updates)
        return self._available if check_updates else self._local

    def is_version_downloaded(self, version: str) -> bool:
        return any(
            e["version"] == version and (e.get("downloaded") or e.get("current"))
            for e in self._local
        )


def _device():
    return SimpleNamespace(
        name="fw1",
        current_version="11.2.7-h15",
        staged_version=None,
        staged_at=None,
        staged_error=None,
        ha_peer_id=None,
    )


def _task():
    return SimpleNamespace(
        id=1, job_id=13, ha_pair_key="pair-1", phase=TaskPhase.PRECHECK,
        confirmation_token=None, progress={}, tick_count=0, error=None,
        device=SimpleNamespace(name="fw1"),
    )


def _job():
    return SimpleNamespace(id=13, target_version="12.1.4-h2")


def _patch_download_recorder(monkeypatch):
    """Replace _download_step with a recorder that always succeeds; returns the
    list of (version, slot) tuples in call order."""
    downloads: list[tuple[str, str]] = []

    def _fake(db, job, task, device, version, *, slot):
        downloads.append((version, slot))
        return _Wait.DONE

    monkeypatch.setattr(upgrade, "_download_step", _fake)
    return downloads


def test_base_detection_falls_back_to_update_server(monkeypatch):
    """The regression guard: a fresh major jump whose local list lacks the target
    train must re-query the update server so the base is found and downloaded
    BEFORE the target. Without the fallback the base is skipped and the target
    never lands."""
    client = _SoftwareListClient(local=LOCAL_NO_TARGET_TRAIN, available=AVAILABLE_WITH_BASE)
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: client)
    downloads = _patch_download_recorder(monkeypatch)

    out = upgrade._phase_ensure_image(MagicMock(), _job(), _task(), _device())

    assert out is True
    # Local-first, THEN the update-server fallback — this IS the fix.
    assert client.list_calls == [False, True]
    # Base (12.1.2) downloaded before the target — the bug skipped the base.
    assert downloads == [("12.1.2", "base"), ("12.1.4-h2", "target")]


def test_base_already_local_skips_update_server_roundtrip(monkeypatch):
    """When the base is already present locally, base_image_from_list finds it in
    the local list — no update-server round-trip (keeps every poll re-entry
    cheap), and only the target downloads."""
    client = _SoftwareListClient(local=LOCAL_WITH_BASE, available=AVAILABLE_WITH_BASE)
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: client)
    downloads = _patch_download_recorder(monkeypatch)

    out = upgrade._phase_ensure_image(MagicMock(), _job(), _task(), _device())

    assert out is True
    assert client.list_calls == [False]  # local only — no check_updates=True
    assert downloads == [("12.1.4-h2", "target")]
