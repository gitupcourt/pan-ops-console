"""Non-blocking confirm/override gates.

The reboot / failover / primary-upgrade / install-hold pauses used to BLOCK the
worker (a `time.sleep` poll loop held a concurrency slot for the whole wait, so
two parked pairs starved every other pair in the job — observed live on job 8).
They are now non-blocking: the gate parks the task at an AWAITING_* phase and
RETURNS, freeing the slot; the /confirm|/override|/rerun route re-dispatches
drive_pair, and on re-entry the gate consumes the token (or marker) and proceeds.

These exercise the REAL marker plumbing (`_mark_phase_done` /
`_phase_already_done` / `_record` / `_set_phase`) against a MagicMock db, so a
break in resume semantics surfaces here rather than in production. Only the
device/IO calls (precheck runner, pan client, route-reachability) are stubbed.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.upgrade.models.enums import TaskPhase
from app.upgrade.services import upgrade
from app.upgrade.services.upgrade import _OverrideOutcome


def _task(**over):
    base = dict(
        id=1,
        job_id=1,
        phase=TaskPhase.PENDING,
        confirmation_token=None,
        progress={},
        tick_count=0,
        error=None,
        device=SimpleNamespace(name="fw1", ha_peer_id=None),
    )
    base.update(over)
    return SimpleNamespace(**base)


def _completed(task):
    return (task.progress or {}).get("completed_phases", [])


# ---------- _confirm_gate (the shared non-blocking primitive) ----------


def test_confirm_gate_full_park_resume_idempotent_cycle():
    """One gate's whole life: park (no token) → resume (token) → stay-advanced
    (marker) on any later re-entry."""
    db = MagicMock()
    task = _task(phase=TaskPhase.UPGRADE_PRIMARY)

    # 1) No token yet → park at the gate phase and STOP (slot frees).
    assert (
        upgrade._confirm_gate(
            db, task, TaskPhase.AWAITING_REBOOT_CONFIRM, "reboot_confirmed",
            parked_msg="awaiting reboot", proceed_msg="rebooting",
        )
        is False
    )
    assert task.phase == TaskPhase.AWAITING_REBOOT_CONFIRM
    assert "reboot_confirmed" not in _completed(task)

    # 2) Operator clicked Confirm → token set, drive_pair re-dispatched.
    task.confirmation_token = "tok"
    assert (
        upgrade._confirm_gate(
            db, task, TaskPhase.AWAITING_REBOOT_CONFIRM, "reboot_confirmed",
            parked_msg="awaiting reboot", proceed_msg="rebooting",
        )
        is True
    )
    assert task.confirmation_token is None  # consumed
    assert "reboot_confirmed" in _completed(task)

    # 3) Any later re-dispatch (e.g. driver re-runs from the top) must NOT
    #    re-park — the marker keeps us past the gate.
    task.phase = TaskPhase.UPGRADE_PRIMARY
    assert (
        upgrade._confirm_gate(
            db, task, TaskPhase.AWAITING_REBOOT_CONFIRM, "reboot_confirmed",
            parked_msg="awaiting reboot", proceed_msg="rebooting",
        )
        is True
    )
    assert task.phase == TaskPhase.UPGRADE_PRIMARY  # untouched


def test_confirm_gate_records_proceed_message_on_resume():
    db = MagicMock()
    task = _task(phase=TaskPhase.AWAITING_FAILOVER_CONFIRM, confirmation_token="tok")
    upgrade._confirm_gate(
        db, task, TaskPhase.AWAITING_FAILOVER_CONFIRM, "failover_confirmed",
        parked_msg="awaiting failover", proceed_msg="failing over now",
    )
    log = (task.progress or {}).get("log", [])
    assert any("failing over now" in line for line in log)


# ---------- _resolve_override_action ----------


def _db_with_job(state):
    db = MagicMock()
    db.get = MagicMock(return_value=SimpleNamespace(state=state))
    return db


def test_resolve_override_none_without_token():
    db = _db_with_job(upgrade.JobState.RUNNING)
    task = _task(confirmation_token=None)
    assert upgrade._resolve_override_action(db, task) is None


def test_resolve_override_proceed_consumes_token():
    db = _db_with_job(upgrade.JobState.RUNNING)
    task = _task(confirmation_token="abc123")
    assert upgrade._resolve_override_action(db, task) == _OverrideOutcome.PROCEED
    assert task.confirmation_token is None


def test_resolve_override_rerun_on_prefix():
    db = _db_with_job(upgrade.JobState.RUNNING)
    task = _task(confirmation_token="RERUN_deadbeef")
    assert upgrade._resolve_override_action(db, task) == _OverrideOutcome.RERUN
    assert task.confirmation_token is None


@pytest.mark.parametrize(
    "state",
    [upgrade.JobState.ABORTED, upgrade.JobState.FAILED, upgrade.JobState.COMPLETED],
)
def test_resolve_override_abort_on_terminal_job(state):
    db = _db_with_job(state)
    # Token present but the job is terminal → ABORT wins (operator aborted).
    task = _task(confirmation_token="abc123")
    assert upgrade._resolve_override_action(db, task) == _OverrideOutcome.ABORT


# ---------- _phase_precheck (override gate, non-blocking) ----------


def _run(sev, *, results=None, error=None, rid=7):
    return SimpleNamespace(
        id=rid, overall_severity=sev, results=results or {}, error=error
    )


@pytest.fixture
def precheck_io(monkeypatch):
    """Stub only the device/DB IO; leave the real marker/record/phase plumbing
    in place. Returns a recorder + a settable precheck result."""
    state = {"run_calls": 0, "result": _run(upgrade.Severity.PASS), "failed": None}

    def _run_precheck(db, device, **kw):
        state["run_calls"] += 1
        return state["result"]

    monkeypatch.setattr(upgrade.precheck_svc, "run_precheck_for_device", _run_precheck)
    monkeypatch.setattr(upgrade, "_warn_if_target_exceeds_panorama", lambda *a, **k: None)
    monkeypatch.setattr(upgrade, "_resolve_checks_for_job", lambda db, job: None)
    monkeypatch.setattr(
        upgrade, "_fail_job", lambda db, jid, reason: state.__setitem__("failed", reason)
    )
    return state


def _precheck_job(**over):
    base = dict(
        id=1, created_by_id=1, target_version="12.1.7",
        auto_ack_precheck_failures=False, pre_stage_mode="none",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_precheck_fail_parks_nonblocking(precheck_io):
    precheck_io["result"] = _run(upgrade.Severity.FAIL, results={"ntp": "x"})
    task = _task(phase=TaskPhase.PENDING)
    out = upgrade._phase_precheck(MagicMock(), _precheck_job(), task, task.device)
    assert out is False  # parked → drive_pair returns, slot frees
    assert task.phase == TaskPhase.AWAITING_PRECHECK_OVERRIDE
    assert "precheck" not in _completed(task)  # NOT advanced
    assert precheck_io["run_calls"] == 1


def test_precheck_resume_proceed_advances_without_rerunning(precheck_io, monkeypatch):
    monkeypatch.setattr(upgrade, "_resolve_override_action", lambda db, t: _OverrideOutcome.PROCEED)
    task = _task(phase=TaskPhase.AWAITING_PRECHECK_OVERRIDE)
    out = upgrade._phase_precheck(MagicMock(), _precheck_job(), task, task.device)
    assert out is True
    assert "precheck" in _completed(task)
    assert precheck_io["run_calls"] == 0  # the check is NOT re-run on override


def test_precheck_resume_rerun_reexecutes(precheck_io, monkeypatch):
    monkeypatch.setattr(upgrade, "_resolve_override_action", lambda db, t: _OverrideOutcome.RERUN)
    precheck_io["result"] = _run(upgrade.Severity.PASS)  # fixed → passes on re-run
    task = _task(phase=TaskPhase.AWAITING_PRECHECK_OVERRIDE)
    out = upgrade._phase_precheck(MagicMock(), _precheck_job(), task, task.device)
    assert out is True
    assert precheck_io["run_calls"] == 1  # re-ran the check
    assert "precheck" in _completed(task)


def test_precheck_resume_abort_stops(precheck_io, monkeypatch):
    monkeypatch.setattr(upgrade, "_resolve_override_action", lambda db, t: _OverrideOutcome.ABORT)
    task = _task(phase=TaskPhase.AWAITING_PRECHECK_OVERRIDE)
    out = upgrade._phase_precheck(MagicMock(), _precheck_job(), task, task.device)
    assert out is False
    assert "precheck" not in _completed(task)
    assert precheck_io["run_calls"] == 0


def test_precheck_resume_none_keeps_parked(precheck_io, monkeypatch):
    monkeypatch.setattr(upgrade, "_resolve_override_action", lambda db, t: None)
    task = _task(phase=TaskPhase.AWAITING_PRECHECK_OVERRIDE)
    out = upgrade._phase_precheck(MagicMock(), _precheck_job(), task, task.device)
    assert out is False
    assert precheck_io["run_calls"] == 0  # still waiting, didn't re-run


def test_precheck_fail_auto_ack_proceeds(precheck_io):
    precheck_io["result"] = _run(upgrade.Severity.FAIL, results={"ntp": "x"})
    task = _task(phase=TaskPhase.PENDING)
    out = upgrade._phase_precheck(
        MagicMock(), _precheck_job(auto_ack_precheck_failures=True), task, task.device
    )
    assert out is True
    assert "precheck" in _completed(task)
    assert task.phase != TaskPhase.AWAITING_PRECHECK_OVERRIDE


def test_precheck_pass_advances(precheck_io):
    task = _task(phase=TaskPhase.PENDING)
    out = upgrade._phase_precheck(MagicMock(), _precheck_job(), task, task.device)
    assert out is True
    assert "precheck" in _completed(task)


# ---------- _phase_postcheck (override gate, non-blocking) ----------


@pytest.fixture
def postcheck_io(monkeypatch):
    state = {"run_calls": 0, "result": _run(upgrade.Severity.PASS)}

    def _run_postcheck(db, device, **kw):
        state["run_calls"] += 1
        return state["result"]

    monkeypatch.setattr(upgrade.precheck_svc, "run_precheck_for_device", _run_postcheck)
    monkeypatch.setattr(upgrade, "_resolve_checks_for_job", lambda db, job: None)
    monkeypatch.setattr(upgrade, "_wait_for_upgrade_route_ready", lambda db, device: True)
    monkeypatch.setattr(upgrade, "_fail_job", lambda db, jid, reason: None)
    return state


def _postcheck_job(**over):
    base = dict(id=1, created_by_id=1, auto_ack_postcheck_failures=False)
    base.update(over)
    return SimpleNamespace(**base)


def test_postcheck_fail_parks_nonblocking(postcheck_io):
    postcheck_io["result"] = _run(upgrade.Severity.FAIL, results={"ha": "x"})
    task = _task(phase=TaskPhase.UPGRADE_PRIMARY)
    out = upgrade._phase_postcheck(MagicMock(), _postcheck_job(), task, task.device)
    assert out is False
    assert task.phase == TaskPhase.AWAITING_POSTCHECK_OVERRIDE
    assert "postcheck" not in _completed(task)


def test_postcheck_resume_proceed_advances_without_rerunning(postcheck_io, monkeypatch):
    monkeypatch.setattr(upgrade, "_resolve_override_action", lambda db, t: _OverrideOutcome.PROCEED)
    task = _task(phase=TaskPhase.AWAITING_POSTCHECK_OVERRIDE)
    out = upgrade._phase_postcheck(MagicMock(), _postcheck_job(), task, task.device)
    assert out is True
    assert "postcheck" in _completed(task)
    assert postcheck_io["run_calls"] == 0


def test_postcheck_pass_advances(postcheck_io):
    task = _task(phase=TaskPhase.UPGRADE_PRIMARY)
    out = upgrade._phase_postcheck(MagicMock(), _postcheck_job(), task, task.device)
    assert out is True
    assert "postcheck" in _completed(task)


# ---------- reboot gate inside _phase_install_and_wait ----------


class _FakeInstallClient:
    def __init__(self):
        self.installs = 0
        self.restarts = 0

    def request_software_install(self, version):
        self.installs += 1
        return "job-123"

    def restart_system(self):
        self.restarts += 1

    def wait_for_ready(self, **kw):
        return None

    def get_system_info(self):
        return {"sw-version": "12.1.7"}


@pytest.fixture
def install_io(monkeypatch):
    client = _FakeInstallClient()
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: client)
    monkeypatch.setattr(upgrade, "_wait_for_install_job", lambda *a, **k: True)
    monkeypatch.setattr(upgrade, "_is_already_at_target", lambda d, v: False)
    from app.upgrade.services import precheck as real_precheck  # noqa: PLC0415
    monkeypatch.setattr(real_precheck, "probe_device", lambda *a, **k: None)
    return client


def _install_job(**over):
    base = dict(id=1, target_version="12.1.7", auto_reboot_after_install=False)
    base.update(over)
    return SimpleNamespace(**base)


def test_reboot_gate_parks_after_install(install_io):
    """auto_reboot off: install happens, then we PARK before reboot — slot frees,
    nothing has rebooted, and 'software_installed' is marked so resume won't
    re-install."""
    task = _task(phase=TaskPhase.PENDING)
    out = upgrade._phase_install_and_wait(
        MagicMock(), _install_job(), task, task.device,
        started_phase=TaskPhase.UPGRADE_PRIMARY,
    )
    assert out is False
    assert install_io.installs == 1
    assert install_io.restarts == 0  # did NOT reboot
    assert task.phase == TaskPhase.AWAITING_REBOOT_CONFIRM
    assert "software_installed" in _completed(task)
    assert "reboot_confirmed" not in _completed(task)
    assert "install_complete" not in _completed(task)


def test_reboot_gate_resume_skips_reinstall_then_reboots(install_io):
    """Re-dispatch after 'Reboot now': the software_installed marker means we do
    NOT re-issue the install — we go straight to the reboot and finish."""
    task = _task(
        phase=TaskPhase.AWAITING_REBOOT_CONFIRM,
        confirmation_token="tok",
        progress={"completed_phases": ["software_installed"]},
    )
    out = upgrade._phase_install_and_wait(
        MagicMock(), _install_job(), task, task.device,
        started_phase=TaskPhase.UPGRADE_PRIMARY,
    )
    assert out is True
    assert install_io.installs == 0  # NOT re-installed
    assert install_io.restarts == 1  # rebooted on resume
    assert "reboot_confirmed" in _completed(task)
    assert "install_complete" in _completed(task)


def test_auto_reboot_skips_gate_entirely(install_io):
    """With auto_reboot_after_install=True there's no pause: install → reboot →
    done in one pass, no AWAITING_REBOOT_CONFIRM."""
    task = _task(phase=TaskPhase.PENDING)
    out = upgrade._phase_install_and_wait(
        MagicMock(), _install_job(auto_reboot_after_install=True), task, task.device,
        started_phase=TaskPhase.UPGRADE_PRIMARY,
    )
    assert out is True
    assert install_io.installs == 1
    assert install_io.restarts == 1
    assert "install_complete" in _completed(task)
