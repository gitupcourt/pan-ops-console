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


def test_jobcreate_pre_stage_mode_invalid_rejected():
    with pytest.raises(ValidationError):
        JobCreate(**_kwargs(pre_stage_mode="bogus"))
