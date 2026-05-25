"""Tests for the passive-wait-timeout state hint mapper.

This runs in the operator's worst moment — an upgrade has timed out in
the middle of a maintenance window — and the hint is the first thing
they read. A wrong or missing hint sends them down the wrong path.
"""

from __future__ import annotations

from app.upgrade.services.upgrade import _passive_state_hint


def test_initial_state_hints_at_heartbeat_or_version_mismatch():
    h = _passive_state_hint("initial")
    assert "initial" in h
    assert "negotiating" in h.lower() or "heartbeat" in h.lower()


def test_non_functional_state_hints_at_dataplane_or_config_sync():
    h = _passive_state_hint("non-functional")
    assert "non-functional" in h
    assert "dataplane" in h.lower() or "mismatch" in h.lower()


def test_suspended_state_hints_at_failed_resume():
    h = _passive_state_hint("suspended")
    assert "suspended" in h
    assert "resume" in h.lower()


def test_active_state_hints_at_preempt():
    """The "I expected passive but got active" case — should NOT scare the
    operator, just explain that preempt fired and offer the manual swap."""
    h = _passive_state_hint("active")
    assert "active" in h
    assert "preempt" in h.lower()


def test_unknown_state_hints_at_mgmt_connectivity():
    h = _passive_state_hint("unknown")
    assert "mgmt-plane" in h.lower() or "ha is configured" in h.lower()


def test_empty_string_treated_as_unknown():
    h = _passive_state_hint("")
    assert "mgmt-plane" in h.lower() or "ha is configured" in h.lower()


def test_case_and_whitespace_normalized():
    """States come from `show high-availability state` and may have
    mixed case / surrounding whitespace from XML parsing."""
    assert _passive_state_hint("  Initial  ") == _passive_state_hint("initial")
    assert _passive_state_hint("SUSPENDED") == _passive_state_hint("suspended")


def test_unrecognized_state_falls_back_with_state_quoted():
    """An exotic state we haven't seen yet should at least name itself
    in the hint so the operator can search for it."""
    h = _passive_state_hint("tentative")
    assert "tentative" in h
    assert "manual inspection" in h.lower() or "not a recognized" in h.lower()
