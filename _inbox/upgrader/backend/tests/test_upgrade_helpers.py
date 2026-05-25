"""Pure-helper tests for the upgrade orchestrator.

These four helpers are tiny but production-critical:

  _is_already_at_target   — decides whether to skip the install dance entirely
  _is_ha_healthy          — gates the both-at-target early-out so we don't
                            skip work when a previous run left HA broken
  _phase_already_done     — drives "Retry job" resume-from-last-step behavior
  _mark_phase_done        — persists the marker that _phase_already_done reads

Together they're the difference between "Retry re-runs everything" (bad —
wastes a maintenance window, may re-trip transient HA states) and "Retry
picks up where the failure happened." The previous regression here is what
prompted this test file.

We use SimpleNamespace + MagicMock instead of real ORM objects because the
helpers do attribute access only — no relationships, no lazy loads.
"""

from __future__ import annotations

from app.services.upgrade import (
    _is_already_at_target,
    _is_ha_healthy,
    _mark_phase_done,
    _phase_already_done,
)


# ---------- _is_already_at_target ----------


def test_already_at_target_exact_match(make_device):
    d = make_device(current_version="11.1.4-h7")
    assert _is_already_at_target(d, "11.1.4-h7") is True


def test_already_at_target_different_version(make_device):
    d = make_device(current_version="10.2.0")
    assert _is_already_at_target(d, "11.1.4-h7") is False


def test_already_at_target_handles_whitespace(make_device):
    """Versions can come in with stray whitespace from probe parsing."""
    d = make_device(current_version="  11.1.4-h7  ")
    assert _is_already_at_target(d, "11.1.4-h7") is True


def test_already_at_target_missing_current_is_false(make_device):
    """No current_version → we can't claim we're at target. Run the flow."""
    d = make_device(current_version=None)
    assert _is_already_at_target(d, "11.1.4-h7") is False


def test_already_at_target_empty_strings_is_false(make_device):
    """Defensive: empty == empty must NOT short-circuit the install."""
    d = make_device(current_version="")
    assert _is_already_at_target(d, "") is False


# ---------- _is_ha_healthy ----------


def test_ha_healthy_active(make_device):
    assert _is_ha_healthy(make_device(ha_state="active")) is True


def test_ha_healthy_passive(make_device):
    assert _is_ha_healthy(make_device(ha_state="passive")) is True


def test_ha_healthy_active_primary(make_device):
    assert _is_ha_healthy(make_device(ha_state="active-primary")) is True


def test_ha_healthy_active_secondary(make_device):
    assert _is_ha_healthy(make_device(ha_state="active-secondary")) is True


def test_ha_healthy_handles_case_and_whitespace(make_device):
    assert _is_ha_healthy(make_device(ha_state="  Active  ")) is True


def test_ha_unhealthy_initial(make_device):
    """'initial' is exactly the state that broke the previous early-out — must
    NOT be considered healthy or we skip needed HA-resume work."""
    assert _is_ha_healthy(make_device(ha_state="initial")) is False


def test_ha_unhealthy_suspended(make_device):
    assert _is_ha_healthy(make_device(ha_state="suspended")) is False


def test_ha_unhealthy_none(make_device):
    assert _is_ha_healthy(make_device(ha_state=None)) is False


# ---------- _phase_already_done / _mark_phase_done ----------


def test_phase_marker_absent_by_default(fake_task):
    assert _phase_already_done(fake_task, "precheck") is False


def test_phase_marker_present_after_mark(fake_task, fake_db):
    _mark_phase_done(fake_db, fake_task, "precheck")
    assert _phase_already_done(fake_task, "precheck") is True
    # And the commit happens — the worker relies on durability across restarts.
    fake_db.commit.assert_called_once()


def test_phase_marker_does_not_duplicate(fake_task, fake_db):
    _mark_phase_done(fake_db, fake_task, "precheck")
    _mark_phase_done(fake_db, fake_task, "precheck")
    assert fake_task.progress["completed_phases"].count("precheck") == 1


def test_phase_marker_preserves_other_markers(fake_task, fake_db):
    _mark_phase_done(fake_db, fake_task, "precheck")
    _mark_phase_done(fake_db, fake_task, "snapshot")
    assert fake_task.progress["completed_phases"] == ["precheck", "snapshot"]


def test_phase_marker_handles_existing_progress(fake_db):
    """progress may already have non-marker keys (download progress %, etc.).
    Marker logic must not clobber them."""
    from types import SimpleNamespace
    task = SimpleNamespace(id=1, progress={"download_progress": 42})
    _mark_phase_done(fake_db, task, "ensure_image")
    assert task.progress["download_progress"] == 42
    assert task.progress["completed_phases"] == ["ensure_image"]


def test_phase_marker_handles_null_progress(fake_db):
    """A freshly-created task may have progress=None — don't crash."""
    from types import SimpleNamespace
    task = SimpleNamespace(id=1, progress=None)
    _mark_phase_done(fake_db, task, "precheck")
    assert task.progress["completed_phases"] == ["precheck"]
