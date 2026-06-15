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
#
# Issue #185 converted the install + reboot WAITS to the same park-and-re-poll
# pattern as the gates: the install command is issued once and its PAN-OS job is
# polled to FIN across re-dispatches; the reboot is issued once and readiness is
# confirmed (reachable AND on target, N consecutive ticks) across re-dispatches.
# So these tests now (a) give the fake client get_job_status (FIN/OK so the
# install job completes promptly) and a get_system_info that reports the target
# version, and (b) drive the phase through its re-dispatches via a small loop —
# every PARKED return re-dispatches drive_pair (here a recorder), exactly as the
# countdown re-dispatch does live. The reboot-gate (confirm) behaviour they pin
# is unchanged; only the surrounding non-blocking waits are new.


class _FakeInstallClient:
    def __init__(self, target="12.1.7"):
        self.installs = 0
        self.restarts = 0
        self.target = target
        self.job_status_calls = 0
        self.sysinfo_calls = 0

    def request_software_install(self, version):
        self.installs += 1
        return "job-123"

    def get_job_status(self, job_id):
        # Install job FINs OK immediately — the wait converges on the first poll.
        self.job_status_calls += 1
        return {"status": "FIN", "progress": "100", "result": "OK"}

    def restart_system(self):
        self.restarts += 1

    def get_system_info(self):
        # Reachable and already on target — each readiness tick counts toward the
        # consecutive-success streak so the reboot converges after a few ticks.
        self.sysinfo_calls += 1
        return SimpleNamespace(sw_version=self.target, ha_state=None)


@pytest.fixture
def install_io(monkeypatch):
    client = _FakeInstallClient()
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: client)
    monkeypatch.setattr(upgrade, "_is_already_at_target", lambda d, v: False)
    # Post-reboot install verification (#186) is out of scope for these
    # reboot-gate tests — their scenario is a successful install — so stub it to
    # "applied". The verify logic itself is covered by test_upgrade_verify_install.
    monkeypatch.setattr(upgrade, "_verify_install_applied", lambda *a, **k: True)
    # Parks re-dispatch drive_pair; record instead of hitting a broker.
    dispatches = []
    monkeypatch.setattr(
        upgrade.drive_pair_task, "apply_async",
        lambda args=(), **kw: dispatches.append((args, kw)),
    )
    client.dispatches = dispatches  # expose for assertions
    from app.upgrade.services import precheck as real_precheck  # noqa: PLC0415
    monkeypatch.setattr(real_precheck, "probe_device", lambda *a, **k: None)
    return client


def _install_job(**over):
    base = dict(
        id=1, job_id=1, ha_pair_key="pair-1",
        target_version="12.1.7", auto_reboot_after_install=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _drive_until_settled(job, task, *, max_ticks=20):
    """Re-invoke _phase_install_and_wait the way the countdown re-dispatch does,
    until it stops parking (returns True, or False with no fresh park). Returns
    the final bool. Mirrors the live park → re-dispatch → re-enter loop so the
    non-blocking install/reboot waits run to completion in a unit test."""
    last = None
    for _ in range(max_ticks):
        # task.job_id / ha_pair_key are threaded through to the re-dispatch.
        task.job_id = job.job_id
        task.ha_pair_key = job.ha_pair_key
        last = upgrade._phase_install_and_wait(
            MagicMock(), job, task, task.device,
            started_phase=TaskPhase.UPGRADE_PRIMARY,
        )
        if last is True:
            return True
        if task.phase in (TaskPhase.AWAITING_REBOOT_CONFIRM, TaskPhase.FAILED):
            return last  # parked at the operator gate / failed — stop driving
    return last


def test_reboot_gate_parks_after_install(install_io):
    """auto_reboot off: the install job runs (issued once, polled to FIN), then we
    PARK at the reboot confirm gate — nothing has rebooted yet and
    'software_installed' is marked so a resume won't re-install."""
    task = _task(phase=TaskPhase.PENDING, job_id=1, ha_pair_key="pair-1")
    out = _drive_until_settled(_install_job(), task)
    assert out is False
    assert install_io.installs == 1  # issued exactly once
    assert install_io.restarts == 0  # did NOT reboot
    assert task.phase == TaskPhase.AWAITING_REBOOT_CONFIRM
    assert "software_installed" in _completed(task)
    assert "reboot_confirmed" not in _completed(task)
    assert "install_complete" not in _completed(task)


def test_install_issued_once_across_repolls(install_io):
    """Idempotency: while the install job is still running (not FIN), re-entries
    must NOT re-issue request_software_install — they only re-poll the persisted
    job id."""
    # get_job_status reports ACT (not FIN) so the wait keeps parking.
    install_io.get_job_status = lambda job_id: {  # type: ignore[assignment]
        "status": "ACT", "progress": "40", "result": "unknown",
    }
    job = _install_job()
    task = _task(phase=TaskPhase.PENDING, job_id=1, ha_pair_key="pair-1")
    for _ in range(4):
        out = upgrade._phase_install_and_wait(
            MagicMock(), job, task, task.device,
            started_phase=TaskPhase.UPGRADE_PRIMARY,
        )
        assert out is False  # parked, re-polling
    assert install_io.installs == 1  # issued ONCE despite 4 entries
    assert task.progress["install_job_id"] == "job-123"
    assert "software_installed" not in _completed(task)


def test_reboot_gate_resume_skips_reinstall_then_reboots(install_io):
    """Re-dispatch after 'Reboot now': the software_installed marker means we do
    NOT re-issue the install — we restart once and converge once the device
    reports the target version on enough consecutive readiness checks."""
    task = _task(
        phase=TaskPhase.AWAITING_REBOOT_CONFIRM,
        confirmation_token="tok",
        progress={"completed_phases": ["software_installed"]},
        job_id=1, ha_pair_key="pair-1",
    )
    out = _drive_until_settled(_install_job(), task)
    assert out is True
    assert install_io.installs == 0  # NOT re-installed
    assert install_io.restarts == 1  # rebooted exactly once
    assert "reboot_confirmed" in _completed(task)
    assert "install_complete" in _completed(task)


def test_reboot_issued_once_across_repolls(install_io):
    """Idempotency: a re-entry must NEVER re-restart a device that's already
    rebooting — restart_system is issued once behind the reboot_issued marker
    while readiness is still being confirmed."""
    # Device not yet back on target → readiness streak never completes → parks.
    install_io.get_system_info = lambda: SimpleNamespace(  # type: ignore[assignment]
        sw_version="11.2.7", ha_state=None
    )
    task = _task(
        phase=TaskPhase.UPGRADE_PRIMARY,
        progress={"completed_phases": ["software_installed", "reboot_confirmed"]},
        job_id=1, ha_pair_key="pair-1",
    )
    job = _install_job()
    for _ in range(4):
        out = upgrade._phase_install_and_wait(
            MagicMock(), job, task, task.device,
            started_phase=TaskPhase.UPGRADE_PRIMARY,
        )
        assert out is False  # parked, re-confirming readiness
    assert install_io.restarts == 1  # restarted ONCE despite 4 entries
    assert "reboot_issued" in _completed(task)
    assert "install_complete" not in _completed(task)


def test_auto_reboot_skips_gate_entirely(install_io):
    """With auto_reboot_after_install=True there's no operator pause: install →
    reboot → done across re-dispatches, never parking at AWAITING_REBOOT_CONFIRM."""
    task = _task(phase=TaskPhase.PENDING, job_id=1, ha_pair_key="pair-1")
    out = _drive_until_settled(_install_job(auto_reboot_after_install=True), task)
    assert out is True
    assert install_io.installs == 1
    assert install_io.restarts == 1
    assert task.phase != TaskPhase.AWAITING_REBOOT_CONFIRM
    assert "install_complete" in _completed(task)
