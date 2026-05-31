"""Tests for the #89 capacity poll split: config-class (slow beat) vs
live-telemetry (fast beat), and the POLL_*_INTERVAL_SECONDS settings.

The split must be a true partition of the catalog — every metric lands in
exactly one beat, and a metric tagged with a brand-new category must ride
the FAST beat by default rather than silently disappearing from polling.
These tests exercise the REAL task predicates (not a re-implementation) by
capturing the metric list each task hands to the poller core.
"""

from __future__ import annotations

import pytest

import app.capacity.tasks as tasks_mod
from app.capacity.services.catalog import MetricSpec
from app.config import Settings


def _fake_catalog() -> list[MetricSpec]:
    """A known mix: config-class, system, traffic, and a deliberately
    unknown future category ('license') to prove the complement framing
    keeps polling it on the fast beat.
    """
    def mk(name: str, category: str) -> MetricSpec:
        # current/max are unused here — the poller core is patched out, so
        # the tasks only read .name / .category off each spec.
        return MetricSpec(
            name=name, category=category, description="", current=None, max=None
        )

    return [
        mk("address_objects", "config"),
        mk("security_policies", "config"),
        mk("dp_cpu", "system"),
        mk("mp_memory", "system"),
        mk("session_table_utilization", "traffic"),
        mk("future_widget", "license"),  # unknown future category
    ]


@pytest.fixture
def captured(monkeypatch):
    """Patch the catalog loader to the known mix and capture the metric
    list each task hands to `poller.poll_all`, without touching a real
    device or running a real poll. Returns the capture list (one entry
    per task invocation, in call order).
    """
    calls: list[list[MetricSpec]] = []

    def fake_core_poll(db, metrics, store):
        calls.append(list(metrics))
        return {}

    monkeypatch.setattr(
        "app.capacity.services.catalog.load_catalog",
        lambda *a, **k: _fake_catalog(),
    )
    monkeypatch.setattr(
        "app.capacity.services.poller.poll_all", fake_core_poll
    )
    return calls


# ---------- the partition ----------

def test_config_task_polls_only_config_category(captured):
    tasks_mod.poll_config_metrics()
    cats = {m.category for m in captured[-1]}
    assert cats == {"config"}


def test_system_task_polls_complement_of_config(captured):
    tasks_mod.poll_system_metrics()
    cats = {m.category for m in captured[-1]}
    assert "config" not in cats
    # The unknown future category must NOT silently vanish — it rides the
    # fast beat by default. This is the "no silent caps" guard.
    assert "license" in cats


def test_split_is_a_partition_no_metric_dropped_or_doubled(captured):
    tasks_mod.poll_config_metrics()
    config_names = {m.name for m in captured[-1]}
    tasks_mod.poll_system_metrics()
    system_names = {m.name for m in captured[-1]}

    all_names = {m.name for m in _fake_catalog()}
    # Union covers the whole catalog (nothing silently unpolled)...
    assert config_names | system_names == all_names
    # ...and the two beats never poll the same metric (no double-read).
    assert config_names & system_names == set()


def test_poll_all_still_polls_everything(captured):
    """The manual full-sweep task is unchanged — it polls the whole
    catalog regardless of category."""
    tasks_mod.poll_all()
    names = {m.name for m in captured[-1]}
    assert names == {m.name for m in _fake_catalog()}


# ---------- interval resolution ----------

def test_system_interval_inherits_legacy_poll_interval():
    """A single-knob install that only set POLL_INTERVAL_SECONDS keeps its
    live-telemetry cadence unchanged: the system beat inherits it."""
    s = Settings(POLL_INTERVAL_SECONDS=137, FERNET_KEY="x" * 32)
    assert s.POLL_SYSTEM_INTERVAL_SECONDS == 137


def test_explicit_system_interval_wins_over_legacy():
    s = Settings(
        POLL_INTERVAL_SECONDS=137,
        POLL_SYSTEM_INTERVAL_SECONDS=45,
        FERNET_KEY="x" * 32,
    )
    assert s.POLL_SYSTEM_INTERVAL_SECONDS == 45


def test_config_interval_defaults_to_one_hour():
    s = Settings(FERNET_KEY="x" * 32)
    assert s.POLL_CONFIG_INTERVAL_SECONDS == 3600
