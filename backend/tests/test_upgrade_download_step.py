"""`_download_step` — refresh the catalog before downloading, and surface the
REAL failure reason (no disk-guessing).

Regression context: a stage-only job where every device failed "did not land".
The base image was present; the target maintenance release downloaded 0 bytes
and the PAN-OS download job FINished with a FAILURE result — but the orchestrator
only checked `status == "FIN"`, ignored the job `result`/`details`, retried 3×,
and reported a misleading "probably disk" guess. Root cause (confirmed on a live
firewall): PAN-OS won't fetch a version whose catalog it hasn't refreshed from
the update server ("Check Now") — even one already shown in `software info`.

Two fixes covered here:
  1. _download_step runs `check_software_updates` (the refresh) BEFORE issuing
     the download request.
  2. On a FIN'd-but-absent download it reads the job RESULT: a FAILURE surfaces
     PAN-OS's actual `details` and fails fast (no blind retry, no "disk" guess);
     an OK-but-not-yet-listed result keeps the bounded re-request.

No-DB SimpleNamespace + MagicMock + monkeypatch style (backend is Python 3.11).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.upgrade.models.enums import TaskPhase
from app.upgrade.services import upgrade
from app.upgrade.services.upgrade import _Wait


class _DLStepClient:
    """Records the order of check/download calls and returns a configurable job
    status + presence, so each branch of _download_step can be driven.

    `is_version_downloaded` reads local state directly (it does NOT go through
    list_software) so it never pollutes the recorded call order."""

    def __init__(self, *, lands: bool, result: str = "OK", details: str = ""):
        self.calls: list[str] = []
        self.checks = 0
        self.downloads = 0
        self._lands = lands
        self._result = result
        self._details = details

    def check_software_updates(self):
        self.checks += 1
        self.calls.append("check")

    def request_software_download(self, version):
        self.downloads += 1
        self.calls.append("download")
        return "dljob-1"

    def get_job_status(self, job_id):
        return {
            "status": "FIN",
            "progress": "100" if (self._lands and self._result == "OK") else "0",
            "result": self._result,
            "details": self._details,
        }

    def is_version_downloaded(self, version):
        # Lands only after the download has actually been requested.
        return self._lands and self.downloads > 0


def _task(**over):
    base = dict(
        id=1, job_id=14, ha_pair_key="pair-1", phase=TaskPhase.DOWNLOADING_IMAGE,
        confirmation_token=None, progress={}, tick_count=0, error=None,
        device=SimpleNamespace(name="fw1", ha_peer_id=None),
    )
    base.update(over)
    return SimpleNamespace(**base)


def _job():
    return SimpleNamespace(id=14, target_version="12.1.4-h2")


def _device():
    return SimpleNamespace(name="fw1")


@pytest.fixture
def io(monkeypatch):
    """Capture re-dispatches + failures; silence the progress-percent writes.
    The real progress/marker/record plumbing runs against a MagicMock db."""
    state = {"dispatch": [], "failed": []}
    monkeypatch.setattr(
        upgrade.drive_pair_task, "apply_async",
        lambda args=(), **kw: state["dispatch"].append((args, kw)),
    )
    monkeypatch.setattr(
        upgrade, "_fail_job",
        lambda db, jid, reason: state["failed"].append((jid, reason)),
    )
    monkeypatch.setattr(upgrade, "_set_job_progress", lambda *a, **k: None)
    return state


def test_checks_updates_before_requesting_download(io, monkeypatch):
    """The fix: 'Check Now' (catalog refresh) runs BEFORE the download request,
    and the happy path lands the image."""
    client = _DLStepClient(lands=True, result="OK")
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: client)

    out = upgrade._download_step(MagicMock(), _job(), _task(), _device(), "12.1.4-h2", slot="target")

    assert out is _Wait.DONE
    assert client.calls[:2] == ["check", "download"]  # refresh strictly before request
    assert client.checks == 1 and client.downloads == 1


def test_failed_download_surfaces_real_reason_and_fails_fast(io, monkeypatch):
    """A job that FINs with result=FAIL surfaces PAN-OS's details verbatim, fails
    fast (issued once, not retried 3×), and does NOT blame disk."""
    records: list[str] = []

    def _rec(db, task, msg, phase=None, **k):
        records.append(msg)
        if phase is not None:
            task.phase = phase

    monkeypatch.setattr(upgrade, "_record", _rec)
    client = _DLStepClient(
        lands=False, result="FAIL",
        details="Failed to download. Cannot download the file from the update server.",
    )
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: client)
    task = _task()

    out = upgrade._download_step(MagicMock(), _job(), task, _device(), "12.1.4-h2", slot="target")

    assert out is _Wait.TIMED_OUT
    assert task.phase == TaskPhase.FAILED
    assert client.downloads == 1          # issued once — NOT retried 3×
    assert io["dispatch"] == []           # failed fast — no re-dispatch
    joined = " ".join(records).lower()
    assert "cannot download the file" in joined   # PAN-OS's real reason, surfaced
    assert "disk" not in joined                   # no reflexive disk guess
    assert io["failed"] and "update server" in io["failed"][0][1].lower()


def test_finished_ok_but_absent_still_bounded_retries(io, monkeypatch):
    """The rare 'job said OK but the image isn't listed yet' case keeps the
    bounded re-request (catalog lag) rather than failing immediately."""
    monkeypatch.setattr(upgrade, "_record", lambda *a, **k: None)
    client = _DLStepClient(lands=False, result="OK")
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: client)
    task = _task()

    out = upgrade._download_step(MagicMock(), _job(), task, _device(), "12.1.4-h2", slot="target")

    assert out is _Wait.PARKED
    assert len(io["dispatch"]) == 1       # re-dispatched for another bounded attempt
    assert task.phase != TaskPhase.FAILED
