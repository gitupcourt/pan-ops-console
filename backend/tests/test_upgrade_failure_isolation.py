"""Failure isolation + the COMPLETED_WITH_ERRORS job state (Job 9).

One device's failure used to fail the WHOLE job (homegateway02's transient
download error marked all of job 9 failed, even though 8/9 devices staged
fine). Now:

  - `_fail_job` records the reason on the job but does NOT flip it to FAILED.
  - `_fail_pair_if_partial` fails an HA pair atomically (you can't half-upgrade
    a pair) so a partner doesn't sit non-terminal forever.
  - `_maybe_mark_job_done` decides the terminal state only once EVERY task is
    terminal: all DONE -> COMPLETED, all FAILED -> FAILED, a mix ->
    COMPLETED_WITH_ERRORS.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.upgrade.models.enums import JobState, TaskPhase
from app.upgrade.services import upgrade


# ---------- _fail_job no longer fails the whole job ----------


def test_fail_job_records_reason_without_failing_the_job():
    job = SimpleNamespace(state=JobState.RUNNING, failure_reason=None)
    db = MagicMock()
    db.get.return_value = job
    upgrade._fail_job(db, 1, "Image download failed for fw9")
    assert job.state == JobState.RUNNING  # isolation — job is NOT failed
    assert job.failure_reason == "Image download failed for fw9"


def test_fail_job_first_write_wins_on_reason():
    job = SimpleNamespace(state=JobState.RUNNING, failure_reason="root cause")
    db = MagicMock()
    db.get.return_value = job
    upgrade._fail_job(db, 1, "later cascade")
    assert job.failure_reason == "root cause"


# ---------- _job_terminal ----------


@pytest.mark.parametrize(
    "state,expected",
    [
        (JobState.PENDING, False),
        (JobState.RUNNING, False),
        (JobState.COMPLETED, True),
        (JobState.COMPLETED_WITH_ERRORS, True),
        (JobState.FAILED, True),
        (JobState.ABORTED, True),
    ],
)
def test_job_terminal(state, expected):
    job = SimpleNamespace(state=state)
    assert upgrade._job_terminal(MagicMock(), job) is expected


# ---------- _fail_pair_if_partial (atomic HA-pair failure) ----------


def test_fail_pair_fails_non_terminal_partner():
    failed = SimpleNamespace(phase=TaskPhase.FAILED, progress={}, error="boom")
    partner = SimpleNamespace(phase=TaskPhase.UPGRADE_PRIMARY, progress={}, error=None)
    upgrade._fail_pair_if_partial(MagicMock(), [failed, partner])
    assert partner.phase == TaskPhase.FAILED  # pair fails atomically


def test_fail_pair_leaves_done_partner_alone():
    failed = SimpleNamespace(phase=TaskPhase.FAILED, progress={}, error="boom")
    done = SimpleNamespace(phase=TaskPhase.DONE, progress={}, error=None)
    upgrade._fail_pair_if_partial(MagicMock(), [failed, done])
    assert done.phase == TaskPhase.DONE  # a genuine success is preserved


def test_fail_pair_noop_when_nothing_failed():
    a = SimpleNamespace(phase=TaskPhase.DONE, progress={}, error=None)
    b = SimpleNamespace(phase=TaskPhase.AWAITING_INSTALL_CONFIRM, progress={}, error=None)
    upgrade._fail_pair_if_partial(MagicMock(), [a, b])
    assert b.phase == TaskPhase.AWAITING_INSTALL_CONFIRM  # untouched (no failure)


# ---------- _maybe_mark_job_done (terminal-state aggregation) ----------


def _mmjd_db(job, tasks):
    db = MagicMock()
    db.get.return_value = job
    db.query.return_value.filter.return_value.all.return_value = tasks
    return db


def _job():
    return SimpleNamespace(state=JobState.RUNNING, failure_reason=None, finished_at=None)


def _t(phase):
    return SimpleNamespace(phase=phase)


def test_all_done_completes():
    job = _job()
    upgrade._maybe_mark_job_done(_mmjd_db(job, [_t(TaskPhase.DONE), _t(TaskPhase.DONE)]), 1)
    assert job.state == JobState.COMPLETED


def test_all_failed_fails():
    job = _job()
    upgrade._maybe_mark_job_done(_mmjd_db(job, [_t(TaskPhase.FAILED), _t(TaskPhase.FAILED)]), 1)
    assert job.state == JobState.FAILED


def test_mixed_completes_with_errors():
    job = _job()
    tasks = [_t(TaskPhase.DONE), _t(TaskPhase.FAILED), _t(TaskPhase.DONE)]
    upgrade._maybe_mark_job_done(_mmjd_db(job, tasks), 1)
    assert job.state == JobState.COMPLETED_WITH_ERRORS
    assert "1 of 3" in (job.failure_reason or "")


def test_not_all_terminal_stays_running():
    job = _job()
    # A parked (held) task is not terminal — the job must stay open.
    tasks = [_t(TaskPhase.DONE), _t(TaskPhase.AWAITING_INSTALL_CONFIRM)]
    upgrade._maybe_mark_job_done(_mmjd_db(job, tasks), 1)
    assert job.state == JobState.RUNNING


def test_already_terminal_is_left_unchanged():
    job = SimpleNamespace(
        state=JobState.COMPLETED_WITH_ERRORS, failure_reason="x", finished_at=None
    )
    db = MagicMock()
    db.get.return_value = job
    upgrade._maybe_mark_job_done(db, 1)
    assert job.state == JobState.COMPLETED_WITH_ERRORS  # no re-compute
