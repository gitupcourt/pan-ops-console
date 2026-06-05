"""Issue #186: an install that didn't actually apply must FAIL, not be marked done.

An install job that "did not FIN cleanly" (usually the device is out of disk and
the image can't be written) can reboot the device straight back onto the OLD
image. `_verify_install_applied` catches that: after install + reboot the device
must be RUNNING the target version, else we clear `software_installed` (so a
Retry genuinely re-issues the install) and fail the task — instead of silently
reporting a non-upgrade as a successful upgrade (branch1fw01 / Branch 3 / HG440
in job 12).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.upgrade.models.enums import TaskPhase
from app.upgrade.services import upgrade


def _device(ver, name="fw1"):
    return SimpleNamespace(current_version=ver, name=name)


def _job(target="12.1.7", jid=12):
    return SimpleNamespace(target_version=target, id=jid)


def _task(markers):
    return SimpleNamespace(
        id=1,
        progress={"completed_phases": list(markers), "log": []},
        phase=TaskPhase.UPGRADE_SECONDARY,
        error=None,
    )


def test_verify_passes_when_on_target(monkeypatch):
    failed = []
    monkeypatch.setattr(upgrade, "_fail_job", lambda *a, **k: failed.append(a))
    task = _task(["software_installed"])
    ok = upgrade._verify_install_applied(
        MagicMock(), _job("12.1.7"), task, _device("12.1.7")
    )
    assert ok is True
    assert failed == []  # nothing failed
    assert "software_installed" in task.progress["completed_phases"]  # marker intact
    assert task.phase == TaskPhase.UPGRADE_SECONDARY  # not flipped to FAILED


def test_verify_fails_when_still_on_old_version(monkeypatch):
    failed = []
    monkeypatch.setattr(upgrade, "_fail_job", lambda db, jid, msg: failed.append((jid, msg)))
    task = _task(["software_installed", "suspend_ha"])
    ok = upgrade._verify_install_applied(
        MagicMock(), _job("12.1.7"), task, _device("11.2.7-h15", name="branch1fw01")
    )
    assert ok is False
    assert task.phase == TaskPhase.FAILED
    # software_installed cleared so Retry re-installs; unrelated markers kept
    assert "software_installed" not in task.progress["completed_phases"]
    assert "suspend_ha" in task.progress["completed_phases"]
    assert failed and failed[0][0] == 12  # _fail_job called with the job id
    log = " ".join(task.progress["log"])
    assert "Install did not apply" in log
    assert "11.2.7-h15" in log  # the actual (wrong) version is surfaced


def test_verify_fails_when_version_unknown(monkeypatch):
    # Post-reboot probe failed -> current_version is stale/None -> we can't
    # confirm the target, so fail safe rather than mark a possible non-upgrade done.
    monkeypatch.setattr(upgrade, "_fail_job", lambda *a, **k: None)
    task = _task(["software_installed"])
    ok = upgrade._verify_install_applied(
        MagicMock(), _job("12.1.7"), task, _device(None)
    )
    assert ok is False
    assert task.phase == TaskPhase.FAILED
    assert "software_installed" not in task.progress["completed_phases"]
