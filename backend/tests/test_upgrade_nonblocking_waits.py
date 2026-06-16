"""Issue #185: the orchestrator's long WAITS are now NON-BLOCKING.

The operator gates were already park-and-re-poll; this change converts the four
long waits the same way — install-job FIN, post-reboot readiness, image-download
FIN, and the HA-resync to 'passive'. Each is now driven by `_await_or_park`,
which checks its condition ONCE per drive_pair entry and, if not yet satisfied,
re-dispatches drive_pair (countdown) and parks so the worker slot frees. With
worker --concurrency 2, two blocking waits used to stall the whole job; parking
lets independent pairs progress in parallel.

These exercise the REAL progress/marker plumbing (`_await_or_park`,
`_progress_*`, `_mark_phase_done`) against a MagicMock db. Only the
device/IO calls (pan client) and the Celery re-dispatch (`drive_pair_task`) are
stubbed — a transient exception, the timeout clock surviving re-entries, and the
issue-once idempotency all surface here rather than in production.

(No DB — same SimpleNamespace + MagicMock + monkeypatch style as the other
test_upgrade_*.py modules; backend is Python 3.11.)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.upgrade.models.enums import TaskPhase
from app.upgrade.services import upgrade
from app.upgrade.services.upgrade import _Wait


def _task(**over):
    base = dict(
        id=1,
        job_id=1,
        ha_pair_key="pair-1",
        phase=TaskPhase.UPGRADE_PRIMARY,
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


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Replace drive_pair_task.apply_async with a recorder so a park doesn't hit
    a broker. Returns the list of (args_tuple, kwargs) for assertions."""
    calls: list[tuple] = []
    monkeypatch.setattr(
        upgrade.drive_pair_task, "apply_async",
        lambda args=(), **kw: calls.append((args, kw)),
    )
    return calls


# ---------- _await_or_park (the shared non-blocking wait primitive) ----------


def test_await_or_park_returns_done_when_condition_met(captured_dispatch):
    db = MagicMock()
    task = _task()
    out = upgrade._await_or_park(
        db, task, marker="w", timeout_s=600, is_done=lambda: True
    )
    assert out is _Wait.DONE
    # The start clock is cleared on a terminal outcome…
    assert "_wait_start::w" not in (task.progress or {})
    # …and DONE does NOT re-dispatch (we're advancing, not parking).
    assert captured_dispatch == []


def test_await_or_park_parks_and_redispatches_when_not_done(captured_dispatch):
    db = MagicMock()
    task = _task()
    out = upgrade._await_or_park(
        db, task, marker="w", timeout_s=600, is_done=lambda: False, poll_s=30
    )
    assert out is _Wait.PARKED
    # Parked → exactly one re-dispatch of drive_pair for THIS (job, pair) with a
    # countdown equal to poll_s, keeping the same queue routing.
    assert len(captured_dispatch) == 1
    args, kwargs = captured_dispatch[0]
    # apply_async is called positionally: apply_async((job_id, ha_pair_key), countdown=...)
    assert args == (task.job_id, task.ha_pair_key)
    assert kwargs.get("countdown") == 30
    # The start clock is PERSISTED across the park so the timeout survives the
    # re-entry that the re-dispatch triggers.
    assert "_wait_start::w" in (task.progress or {})


def test_await_or_park_uses_default_poll_when_unspecified(captured_dispatch):
    db = MagicMock()
    task = _task()
    upgrade._await_or_park(db, task, marker="w", timeout_s=600, is_done=lambda: False)
    assert captured_dispatch[0][1]["countdown"] == upgrade.NONBLOCKING_POLL_S


def test_await_or_park_times_out_when_persisted_start_exceeds_timeout(captured_dispatch):
    db = MagicMock()
    # Pre-seed an OLD start so the very first check is already past the timeout —
    # simulates many prior re-entries that never satisfied the condition.
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    task = _task(progress={"_wait_start::w": old})
    out = upgrade._await_or_park(
        db, task, marker="w", timeout_s=60, is_done=lambda: False
    )
    assert out is _Wait.TIMED_OUT
    # Timed out → clock cleared, and NO re-dispatch (the caller fails the task).
    assert "_wait_start::w" not in (task.progress or {})
    assert captured_dispatch == []


def test_await_or_park_done_wins_even_if_past_timeout(captured_dispatch):
    """If the condition is satisfied we return DONE regardless of the clock —
    is_done is checked before the timeout comparison."""
    db = MagicMock()
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    task = _task(progress={"_wait_start::w": old})
    out = upgrade._await_or_park(
        db, task, marker="w", timeout_s=60, is_done=lambda: True
    )
    assert out is _Wait.DONE
    assert captured_dispatch == []


def test_await_or_park_transient_exception_is_treated_as_not_done(captured_dispatch):
    """A transient error from is_done (e.g. device/Panorama mid-reboot) counts as
    'not done yet' and parks — it must NOT abort the wait."""
    db = MagicMock()
    task = _task()

    def _boom():
        raise ConnectionError("device still rebooting")

    out = upgrade._await_or_park(
        db, task, marker="w", timeout_s=600, is_done=_boom
    )
    assert out is _Wait.PARKED
    assert len(captured_dispatch) == 1  # re-dispatched, not failed


def test_await_or_park_persists_start_on_first_check(captured_dispatch):
    """The first check stamps the start clock; a second check reuses it rather
    than resetting (so the timeout actually advances across re-entries)."""
    db = MagicMock()
    task = _task()
    upgrade._await_or_park(db, task, marker="w", timeout_s=600, is_done=lambda: False)
    first = task.progress["_wait_start::w"]
    # Simulate a re-entry: the persisted start must be unchanged.
    upgrade._await_or_park(db, task, marker="w", timeout_s=600, is_done=lambda: False)
    assert task.progress["_wait_start::w"] == first


# ---------- install: issue-once / poll-on-re-entry idempotency ----------


class _InstallPollClient:
    """Fake pan client: counts install requests, returns a configurable job
    status. Used to prove a re-entry with install_job_id set re-polls instead of
    re-issuing."""

    def __init__(self, *, status="ACT", result="unknown", details=""):
        self.installs = 0
        self._status = status
        self._result = result
        self._details = details

    def request_software_install(self, version):
        self.installs += 1
        return "instjob-1"

    def get_job_status(self, job_id):
        return {
            "status": self._status, "progress": "50",
            "result": self._result, "details": self._details,
        }


def _job(**over):
    base = dict(id=12, target_version="12.1.7")
    base.update(over)
    return SimpleNamespace(**base)


def test_install_step_does_not_reissue_when_job_id_present(captured_dispatch, monkeypatch):
    """Idempotency (the core #185 safety): a re-entry that already has a persisted
    install_job_id must NOT call request_software_install again — it only checks
    that job's status. The install command is issued exactly once."""
    client = _InstallPollClient(status="ACT")  # still running → parks
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: client)
    monkeypatch.setattr(upgrade, "_set_substep", lambda *a, **k: None)
    task = _task(progress={"install_job_id": "instjob-1"})

    out = upgrade._install_step(MagicMock(), _job(), task, task.device)

    assert out is _Wait.PARKED
    assert client.installs == 0  # NOT re-issued — only re-polled
    assert "software_installed" not in _completed(task)
    assert len(captured_dispatch) == 1  # parked → re-dispatched


def test_install_step_issues_once_then_polls_to_done(captured_dispatch, monkeypatch):
    """First entry issues the install (no job id yet) and, with the job FIN/OK on
    the same tick, marks software_installed and clears the handle."""
    client = _InstallPollClient(status="FIN", result="OK")
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: client)
    monkeypatch.setattr(upgrade, "_set_substep", lambda *a, **k: None)
    task = _task(progress={})

    out = upgrade._install_step(MagicMock(), _job(), task, task.device)

    assert out is _Wait.DONE
    assert client.installs == 1
    assert "software_installed" in _completed(task)
    # Handle cleared so a later install (partner / reconcile) re-issues cleanly.
    assert "install_job_id" not in (task.progress or {})
    assert captured_dispatch == []  # DONE in one tick → no park


def test_install_step_transient_error_reissues_via_park(captured_dispatch, monkeypatch):
    """A transient 'a software install is in progress' at issue time parks + will
    re-issue on the next entry (bounded by INSTALL_RETRY_ATTEMPTS), instead of
    blocking in a sleep loop. No job id gets persisted from the failed attempt."""
    class _Busy:
        def __init__(self):
            self.installs = 0

        def request_software_install(self, version):
            self.installs += 1
            raise RuntimeError(
                "software install request failed: A software install is in "
                "progress. Please try again later"
            )

    client = _Busy()
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: client)
    monkeypatch.setattr(upgrade, "_set_substep", lambda *a, **k: None)
    task = _task(progress={})

    out = upgrade._install_step(MagicMock(), _job(), task, task.device)

    assert out is _Wait.PARKED
    assert client.installs == 1
    assert "install_job_id" not in (task.progress or {})  # nothing persisted
    assert task.progress.get("install_attempt") == 1  # counter advanced
    assert len(captured_dispatch) == 1


def test_install_step_permanent_error_fails(monkeypatch):
    """A non-transient install error fails the task (TIMED_OUT) — no endless park."""
    class _NoDisk:
        def request_software_install(self, version):
            raise RuntimeError("install request failed: not enough disk space")

    failed = []
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: _NoDisk())
    monkeypatch.setattr(upgrade, "_set_substep", lambda *a, **k: None)
    monkeypatch.setattr(upgrade, "_fail_job", lambda db, jid, reason: failed.append((jid, reason)))
    task = _task(progress={})

    out = upgrade._install_step(MagicMock(), _job(), task, task.device)

    assert out is _Wait.TIMED_OUT
    assert task.phase == TaskPhase.FAILED
    assert failed and failed[0][0] == 12


def test_install_step_failed_result_fails_fast_without_reboot(captured_dispatch, monkeypatch):
    """A FIN'd install job whose RESULT is a failure must NOT mark
    software_installed — so the caller (_phase_install_and_wait) never proceeds
    to reboot a box whose install didn't take. It fails fast with PAN-OS's real
    reason. Regression: the old code logged 'proceeding to restart anyway' and
    rebooted a suspended HA member after a failed install."""
    client = _InstallPollClient(
        status="FIN", result="FAIL",
        details="Failed to install 12.1.7. Content version 8000 is too old.",
    )
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: client)
    monkeypatch.setattr(upgrade, "_set_substep", lambda *a, **k: None)
    failed: list = []
    monkeypatch.setattr(upgrade, "_fail_job", lambda db, jid, reason: failed.append(reason))
    task = _task(
        progress={},
        device=SimpleNamespace(name="fw1", ha_peer_id=None, current_version="11.2.7-h15"),
    )

    out = upgrade._install_step(MagicMock(), _job(), task, task.device)

    assert out is _Wait.TIMED_OUT
    assert client.installs == 1                           # issued once, not retried
    assert "software_installed" not in _completed(task)   # caller will NOT reboot
    assert task.phase == TaskPhase.FAILED
    assert captured_dispatch == []                        # failed fast, not parked
    # PAN-OS's real reason is surfaced, not a guess.
    assert failed and "content version 8000 is too old" in failed[0].lower()


# ---------- reboot: restart issued once, readiness streak ----------


class _RebootClient:
    def __init__(self, *, version="12.1.7"):
        self.restarts = 0
        self.version = version

    def restart_system(self):
        self.restarts += 1

    def get_system_info(self):
        return SimpleNamespace(sw_version=self.version, ha_state=None)


class _RebootWrongVersionClient:
    """Goes unreachable once (it really rebooted), then comes back on the OLD
    version forever — i.e. the new image did not boot (failed upgrade)."""

    def __init__(self, *, old="11.2.7-h15"):
        self.restarts = 0
        self.probes = 0
        self.old = old

    def restart_system(self):
        self.restarts += 1

    def get_system_info(self):
        self.probes += 1
        if self.probes == 1:
            raise ConnectionError("mgmt plane unreachable (rebooting)")
        return SimpleNamespace(sw_version=self.old, ha_state="passive")


def test_reboot_step_needs_consecutive_successes(captured_dispatch, monkeypatch):
    """The reboot wait requires REBOOT_READY_CONSECUTIVE consecutive healthy
    checks (reachable AND on target) before declaring the device up — a single
    good check parks, not done."""
    client = _RebootClient(version="12.1.7")
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: client)
    monkeypatch.setattr(upgrade, "_set_substep", lambda *a, **k: None)
    task = _task(progress={})
    job = _job(target_version="12.1.7")

    # First entry: issues restart once, +1 streak, still short of the threshold.
    out = upgrade._reboot_step(MagicMock(), job, task, task.device)
    assert out is _Wait.PARKED
    assert client.restarts == 1
    assert task.progress["reboot_ok_streak"] == 1
    assert "reboot_issued" in _completed(task)

    # Drive until the streak reaches the threshold; restart never re-issued.
    ticks = 1
    while out is _Wait.PARKED and ticks < 10:
        out = upgrade._reboot_step(MagicMock(), job, task, task.device)
        ticks += 1
    assert out is _Wait.DONE
    assert client.restarts == 1  # issued exactly once across all the re-polls
    assert ticks == upgrade.REBOOT_READY_CONSECUTIVE


def test_reboot_step_wrong_version_resets_streak(captured_dispatch, monkeypatch):
    """A reachable-but-wrong-version response (still on the old image mid-reboot)
    resets the streak — it must not count toward 'up'."""
    client = _RebootClient(version="11.2.7")  # NOT the target
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: client)
    monkeypatch.setattr(upgrade, "_set_substep", lambda *a, **k: None)
    task = _task(progress={"completed_phases": ["reboot_issued"], "reboot_ok_streak": 2})
    job = _job(target_version="12.1.7")

    out = upgrade._reboot_step(MagicMock(), job, task, task.device)
    assert out is _Wait.PARKED
    assert client.restarts == 0  # already issued
    assert task.progress["reboot_ok_streak"] == 0  # reset by the wrong version


def test_reboot_step_came_back_on_old_version_fails_fast(captured_dispatch, monkeypatch):
    """Job-16 regression: a box that reboots onto the OLD image (failed upgrade)
    must fail FAST with an accurate reason once it's stably back on the wrong
    version — not sit through the full REBOOT_TIMEOUT_S reporting 'did not come
    back online' while the box is, in fact, up."""
    records: list = []

    def _rec(db, task, msg, phase=None, **k):
        records.append(msg)
        if phase is not None:
            task.phase = phase

    failed: list = []
    monkeypatch.setattr(upgrade, "_record", _rec)
    monkeypatch.setattr(upgrade, "_set_substep", lambda *a, **k: None)
    monkeypatch.setattr(upgrade, "_fail_job", lambda db, jid, reason: failed.append(reason))
    client = _RebootWrongVersionClient(old="11.2.7-h15")
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: client)
    task = _task(progress={})
    job = _job(target_version="12.1.4-h2")

    out = _Wait.PARKED
    ticks = 0
    while out is _Wait.PARKED and ticks < 15:
        out = upgrade._reboot_step(MagicMock(), job, task, task.device)
        ticks += 1

    assert out is _Wait.TIMED_OUT
    assert client.restarts == 1                  # restart issued exactly once
    assert task.phase == TaskPhase.FAILED
    # Failed FAST: went-offline tick + a short wrong-version streak + the fail
    # entry — nowhere near the 30-min timeout's worth of 30s polls.
    assert ticks <= upgrade.REBOOT_WRONG_VERSION_LIMIT + 3
    joined = " ".join(records).lower()
    assert "did not apply" in joined         # accurate reason, not "did not come back"
    assert "11.2.7-h15" in joined            # names the version it actually booted
    assert failed and "11.2.7-h15" in failed[0]


# ---------- wait_for_passive: single-shot HA-state check ----------


class _HAClient:
    def __init__(self, state):
        self.state = state

    def get_system_info(self):
        return SimpleNamespace(sw_version="12.1.7", ha_state=self.state)


def test_passive_step_done_on_safe_state(captured_dispatch, monkeypatch):
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: _HAClient("passive"))
    monkeypatch.setattr(upgrade, "_set_substep", lambda *a, **k: None)
    task = _task(progress={})
    out = upgrade._passive_step(MagicMock(), _job(), task, task.device)
    assert out is _Wait.DONE
    assert captured_dispatch == []


def test_passive_step_parks_on_unsafe_state(captured_dispatch, monkeypatch):
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: _HAClient("initial"))
    monkeypatch.setattr(upgrade, "_set_substep", lambda *a, **k: None)
    task = _task(progress={})
    out = upgrade._passive_step(MagicMock(), _job(), task, task.device)
    assert out is _Wait.PARKED
    assert len(captured_dispatch) == 1
    # The poll cadence is the HA-specific one, not the default.
    assert captured_dispatch[0][1]["countdown"] == upgrade.HA_PASSIVE_POLL_S


def test_passive_step_timeout_stashes_diagnostics(captured_dispatch, monkeypatch):
    """On timeout, _passive_step stashes the SAME wait_for_passive_diagnostics
    block the old blocking loop did, so _format_passive_timeout_message keeps
    rendering the rich failure message."""
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: _HAClient("suspended"))
    monkeypatch.setattr(upgrade, "_set_substep", lambda *a, **k: None)
    # Pre-seed an expired clock and a state history (as if accrued over re-entries).
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    task = _task(progress={
        "_wait_start::wait_for_passive": old,
        "_passive_states_seen": ["initial", "suspended"],
        "_passive_last_state": "suspended",
    })
    out = upgrade._passive_step(MagicMock(), _job(), task, task.device)
    assert out is _Wait.TIMED_OUT
    diag = task.progress["wait_for_passive_diagnostics"]
    assert diag["last_state"] == "suspended"
    assert diag["states_seen"] == ["initial", "suspended"]
    assert diag["timeout_s"] == upgrade.HA_PASSIVE_TIMEOUT_S
