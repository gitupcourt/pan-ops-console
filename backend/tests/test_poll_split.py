"""Tests for the #89 metric-class split + poll-interval settings.

The config/system class split now lives in `_metric_in_class` (used by the
dispatcher's per-device task and the manual full sweep). The split must be a
true partition — every metric in exactly one class, and a metric tagged with
a brand-new category rides the FAST (system) class by default rather than
silently disappearing.
"""

from __future__ import annotations

import app.capacity.tasks as tasks_mod
from app.capacity.services.catalog import MetricSpec
from app.capacity.tasks import _metric_in_class
from app.config import Settings


def _fake_catalog() -> list[MetricSpec]:
    """A known mix: config-class, system, traffic, and a deliberately unknown
    future category ('license') to prove the complement framing keeps polling
    it on the fast (system) class."""
    def mk(name: str, category: str) -> MetricSpec:
        return MetricSpec(name=name, category=category, description="", current=None, max=None)

    return [
        mk("address_objects", "config"),
        mk("security_policies", "config"),
        mk("dp_cpu", "system"),
        mk("session_table_utilization", "traffic"),
        mk("future_widget", "license"),  # unknown future category
    ]


# ---------- the class partition ----------

def test_metric_in_class_is_a_partition():
    cat = _fake_catalog()
    config = {m.name for m in cat if _metric_in_class(m, "config")}
    system = {m.name for m in cat if _metric_in_class(m, "system")}
    allnames = {m.name for m in cat}

    # config class = exactly the config-category metrics
    assert config == {"address_objects", "security_policies"}
    # union covers everything (nothing silently unpolled)...
    assert config | system == allnames
    # ...and the two classes never overlap (no double-poll)
    assert config & system == set()
    # the unknown future category rides the FAST (system) class
    assert "future_widget" in system


def test_poll_all_still_polls_everything(monkeypatch):
    """The manual full-sweep task is unchanged — polls the whole catalog."""
    calls: list[list] = []

    def fake_core_poll(db, metrics, store):
        calls.append(list(metrics))
        return {}

    monkeypatch.setattr(
        "app.capacity.services.catalog.load_catalog", lambda *a, **k: _fake_catalog()
    )
    monkeypatch.setattr("app.capacity.services.poller.poll_all", fake_core_poll)

    tasks_mod.poll_all()
    assert {m.name for m in calls[-1]} == {m.name for m in _fake_catalog()}


# ---------- interval resolution ----------

def test_system_interval_inherits_legacy_poll_interval():
    """A single-knob install that only set POLL_INTERVAL_SECONDS keeps its
    live-telemetry cadence: the system interval inherits it."""
    s = Settings(POLL_INTERVAL_SECONDS=137, FERNET_KEY="x" * 32)
    assert s.POLL_SYSTEM_INTERVAL_SECONDS == 137


def test_explicit_system_interval_wins_over_legacy():
    s = Settings(
        POLL_INTERVAL_SECONDS=137,
        POLL_SYSTEM_INTERVAL_SECONDS=45,
        FERNET_KEY="x" * 32,
    )
    assert s.POLL_SYSTEM_INTERVAL_SECONDS == 45


def test_config_interval_defaults_to_twice_daily():
    s = Settings(FERNET_KEY="x" * 32)
    assert s.POLL_CONFIG_INTERVAL_SECONDS == 43200  # 12h
