"""Stage-resilience fixes from Job 9.

  - Software download retries transient "Another download is in progress"
    errors instead of failing the device outright.
  - Stage / Stage+Hold modes auto-acknowledge precheck FAILs (and tolerate a
    precheck that couldn't run) — staging only downloads an image, so prechecks
    are informational and must not gate it.
  - The Stage+Hold gate parks BOTH members of an HA pair (not just the gate
    member, which used to sit at DOWNLOADING_IMAGE looking stuck), and Proceed
    works from either member.

Real marker/record/phase plumbing against a MagicMock db; only device/IO calls
are stubbed.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.upgrade.services import upgrade
from app.upgrade.models.enums import Severity, TaskPhase


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


# ---------- transient download detection + retry ----------


def test_is_transient_download_error():
    assert upgrade._is_transient_download_error(
        Exception("software download request failed: Another download is in progress. Please try again later")
    )
    assert upgrade._is_transient_download_error(Exception("a download is in progress"))
    assert not upgrade._is_transient_download_error(Exception("not enough disk space"))
    assert not upgrade._is_transient_download_error(Exception("Connection refused"))


class _DLClient:
    """request_software_download fails `fail_times` times (transient or not),
    then succeeds."""

    def __init__(self, fail_times=0, transient=True):
        self.attempts = 0
        self.fail_times = fail_times
        self.transient = transient

    def request_software_download(self, version):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            reason = (
                "Another download is in progress. Please try again later"
                if self.transient
                else "not enough disk space"
            )
            raise RuntimeError(f"software download request failed: {reason}")
        return "dljob-1"

    def is_version_downloaded(self, version):
        return True


@pytest.fixture
def download_io(monkeypatch):
    monkeypatch.setattr(upgrade.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(upgrade, "_wait_for_download_job", lambda *a, **k: True)
    monkeypatch.setattr(upgrade, "_record", lambda *a, **k: None)


def test_download_retries_transient_then_succeeds(download_io):
    client = _DLClient(fail_times=2, transient=True)
    upgrade._download_image_with_retry(client, "12.1.7", task=_task(), db=MagicMock())
    assert client.attempts == 3  # two transient failures, third succeeded


def test_download_nontransient_raises_immediately(download_io):
    client = _DLClient(fail_times=1, transient=False)
    with pytest.raises(Exception):
        upgrade._download_image_with_retry(client, "12.1.7", task=_task(), db=MagicMock())
    assert client.attempts == 1  # not retried — not a transient error


def test_download_exhausts_retries_and_raises(download_io):
    client = _DLClient(fail_times=99, transient=True)
    with pytest.raises(Exception):
        upgrade._download_image_with_retry(client, "12.1.7", task=_task(), db=MagicMock())
    assert client.attempts == upgrade.DOWNLOAD_RETRY_ATTEMPTS


# ---------- stage modes auto-ack precheck ----------


def _run_obj(sev, *, results=None, error=None, rid=7):
    return SimpleNamespace(id=rid, overall_severity=sev, results=results or {}, error=error)


@pytest.fixture
def precheck_io(monkeypatch):
    state = {"run_calls": 0, "result": _run_obj(Severity.FAIL, results={"ntp": "x"}), "raise": None}

    def _run_precheck(db, device, **kw):
        state["run_calls"] += 1
        if state["raise"] is not None:
            raise state["raise"]
        return state["result"]

    monkeypatch.setattr(upgrade.precheck_svc, "run_precheck_for_device", _run_precheck)
    monkeypatch.setattr(upgrade, "_warn_if_target_exceeds_panorama", lambda *a, **k: None)
    monkeypatch.setattr(upgrade, "_resolve_checks_for_job", lambda db, job: None)
    monkeypatch.setattr(
        upgrade, "_failing_check_names",
        lambda results: list(results.keys()) if results else [],
    )
    monkeypatch.setattr(upgrade, "_fail_job", lambda db, jid, reason: state.__setitem__("failed", reason))
    return state


def _job(mode="hold", auto_ack=False):
    return SimpleNamespace(
        id=1, created_by_id=1, target_version="12.1.7",
        pre_stage_mode=mode, auto_ack_precheck_failures=auto_ack,
    )


@pytest.mark.parametrize("mode", ["hold", "stage_only"])
def test_stage_mode_autoacks_precheck_fail(precheck_io, mode):
    precheck_io["result"] = _run_obj(Severity.FAIL, results={"ntp": "x"})
    task = _task()
    assert upgrade._phase_precheck(MagicMock(), _job(mode=mode), task, task.device) is True
    assert "precheck" in _completed(task)
    assert task.phase != TaskPhase.AWAITING_PRECHECK_OVERRIDE


def test_full_mode_still_parks_on_precheck_fail(precheck_io):
    precheck_io["result"] = _run_obj(Severity.FAIL, results={"ntp": "x"})
    task = _task()
    assert upgrade._phase_precheck(MagicMock(), _job(mode="none"), task, task.device) is False
    assert task.phase == TaskPhase.AWAITING_PRECHECK_OVERRIDE


def test_stage_mode_tolerates_precheck_exception(precheck_io):
    precheck_io["raise"] = RuntimeError("ParseError: not well-formed")
    task = _task()
    assert upgrade._phase_precheck(MagicMock(), _job(mode="hold"), task, task.device) is True
    assert "precheck" in _completed(task)
    assert task.phase != TaskPhase.FAILED


def test_full_mode_fails_on_precheck_exception(precheck_io):
    precheck_io["raise"] = RuntimeError("ParseError: not well-formed")
    task = _task()
    assert upgrade._phase_precheck(MagicMock(), _job(mode="none"), task, task.device) is False
    assert task.phase == TaskPhase.FAILED


def test_stage_mode_passes_precheck_through_when_clean(precheck_io):
    precheck_io["result"] = _run_obj(Severity.PASS)
    task = _task()
    assert upgrade._phase_precheck(MagicMock(), _job(mode="hold"), task, task.device) is True
    assert "precheck" in _completed(task)


# ---------- pair-aware Stage+Hold gate ----------


def test_hold_gate_pair_parks_both_members():
    job = SimpleNamespace(pre_stage_mode="hold")
    passive = _task(id=1, phase=TaskPhase.DOWNLOADING_IMAGE)
    active = _task(id=2, phase=TaskPhase.DOWNLOADING_IMAGE)
    out = upgrade._pre_stage_hold_gate(MagicMock(), job, passive, pair_tasks=[passive, active])
    assert out is False
    # BOTH show the held state — the active no longer sits at DOWNLOADING_IMAGE.
    assert passive.phase == TaskPhase.AWAITING_INSTALL_CONFIRM
    assert active.phase == TaskPhase.AWAITING_INSTALL_CONFIRM


def test_hold_gate_pair_proceeds_on_token_from_either_member():
    job = SimpleNamespace(pre_stage_mode="hold")
    passive = _task(id=1, phase=TaskPhase.AWAITING_INSTALL_CONFIRM, confirmation_token=None)
    active = _task(id=2, phase=TaskPhase.AWAITING_INSTALL_CONFIRM, confirmation_token="tok")
    out = upgrade._pre_stage_hold_gate(MagicMock(), job, passive, pair_tasks=[passive, active])
    assert out is True
    # Token consumed on both; install_confirmed marked on the gate (passive).
    assert passive.confirmation_token is None
    assert active.confirmation_token is None
    assert "install_confirmed" in _completed(passive)


def test_hold_gate_solo_unchanged():
    job = SimpleNamespace(pre_stage_mode="hold")
    task = _task(phase=TaskPhase.DOWNLOADING_IMAGE)
    assert upgrade._pre_stage_hold_gate(MagicMock(), job, task) is False
    assert task.phase == TaskPhase.AWAITING_INSTALL_CONFIRM
