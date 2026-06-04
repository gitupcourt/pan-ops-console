"""Pre-stage: stage-only jobs run precheck + image download + pre-snapshot,
then STOP before install (for pre-staging a fleet ahead of a maintenance
window). Covers the orchestrator gate + the JobCreate validation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.upgrade.models.enums import TaskPhase
from app.upgrade.schemas import JobCreate
from app.upgrade.services import upgrade


# ---------- _pre_stage_stop (orchestrator gate) ----------

def test_pre_stage_stop_none_proceeds(monkeypatch):
    # A normal (full) job must NOT be stopped or marked done — proceed to
    # install.
    actions: list = []
    monkeypatch.setattr(upgrade, "_set_phase", lambda db, t, p: actions.append(p))
    monkeypatch.setattr(upgrade, "_record", lambda db, t, m, **k: actions.append("record"))

    job = SimpleNamespace(pre_stage_mode="none")
    assert upgrade._pre_stage_stop(None, job, [SimpleNamespace()]) is False
    assert actions == []  # nothing staged


def test_pre_stage_stop_stage_only_marks_done(monkeypatch):
    # stage_only: stop (return True) and set every task to DONE.
    phases: list = []
    monkeypatch.setattr(upgrade, "_set_phase", lambda db, t, p: phases.append(p))
    monkeypatch.setattr(upgrade, "_record", lambda db, t, m, **k: None)

    job = SimpleNamespace(pre_stage_mode="stage_only")
    t1, t2 = SimpleNamespace(), SimpleNamespace()
    assert upgrade._pre_stage_stop(None, job, [t1, t2]) is True
    assert phases == [TaskPhase.DONE, TaskPhase.DONE]


def test_pre_stage_stop_missing_attr_proceeds(monkeypatch):
    # Defensive: a job object without the attr (None) behaves like "none".
    monkeypatch.setattr(upgrade, "_set_phase", lambda db, t, p: None)
    monkeypatch.setattr(upgrade, "_record", lambda db, t, m, **k: None)
    job = SimpleNamespace(pre_stage_mode=None)
    assert upgrade._pre_stage_stop(None, job, [SimpleNamespace()]) is False


# ---------- JobCreate.pre_stage_mode validation ----------

def _kwargs(**over) -> dict:
    base = dict(
        name="job",
        target_version="12.1.7",
        device_ids=[1],
        device_pull_image=True,
    )
    base.update(over)
    return base


def test_jobcreate_pre_stage_mode_defaults_to_none():
    assert JobCreate(**_kwargs()).pre_stage_mode == "none"


def test_jobcreate_pre_stage_mode_stage_only_ok():
    assert JobCreate(**_kwargs(pre_stage_mode="stage_only")).pre_stage_mode == "stage_only"


def test_jobcreate_pre_stage_mode_hold_ok():
    assert JobCreate(**_kwargs(pre_stage_mode="hold")).pre_stage_mode == "hold"


def test_jobcreate_pre_stage_mode_invalid_rejected():
    with pytest.raises(ValidationError):
        JobCreate(**_kwargs(pre_stage_mode="bogus"))


# ---------- _pre_stage_hold_gate (non-blocking gate) ----------

class _FakeDB:
    def refresh(self, obj):  # no-op
        pass

    def commit(self):  # no-op
        pass


def test_hold_gate_not_hold_proceeds(monkeypatch):
    monkeypatch.setattr(upgrade, "_phase_already_done", lambda t, m: False)
    job = SimpleNamespace(pre_stage_mode="none")
    task = SimpleNamespace(confirmation_token=None)
    assert upgrade._pre_stage_hold_gate(_FakeDB(), job, task) is True


def test_hold_gate_parks_when_no_token(monkeypatch):
    # No confirmation yet → park at AWAITING_INSTALL_CONFIRM + return False so
    # drive_pair exits (freeing the slot). No marker set.
    rec: dict = {}
    monkeypatch.setattr(upgrade, "_phase_already_done", lambda t, m: False)
    monkeypatch.setattr(
        upgrade, "_record", lambda db, t, m, **k: rec.update(phase=k.get("phase"))
    )
    job = SimpleNamespace(pre_stage_mode="hold")
    task = SimpleNamespace(confirmation_token=None)
    assert upgrade._pre_stage_hold_gate(_FakeDB(), job, task) is False
    assert rec["phase"] == TaskPhase.AWAITING_INSTALL_CONFIRM


def test_hold_gate_proceeds_on_confirm_token(monkeypatch):
    # Re-dispatched after "Proceed to install": token present → consume it,
    # set the marker, proceed.
    marks: list = []
    monkeypatch.setattr(upgrade, "_phase_already_done", lambda t, m: False)
    monkeypatch.setattr(upgrade, "_mark_phase_done", lambda db, t, m: marks.append(m))
    monkeypatch.setattr(upgrade, "_record", lambda db, t, m, **k: None)
    job = SimpleNamespace(pre_stage_mode="hold")
    task = SimpleNamespace(confirmation_token="CONFIRM_abc")
    assert upgrade._pre_stage_hold_gate(_FakeDB(), job, task) is True
    assert task.confirmation_token is None  # consumed
    assert "install_confirmed" in marks


def test_hold_gate_proceeds_when_already_confirmed(monkeypatch):
    # On a later re-dispatch the marker is set → never re-park.
    monkeypatch.setattr(
        upgrade, "_phase_already_done", lambda t, m: m == "install_confirmed"
    )
    job = SimpleNamespace(pre_stage_mode="hold")
    task = SimpleNamespace(confirmation_token=None)
    assert upgrade._pre_stage_hold_gate(_FakeDB(), job, task) is True
