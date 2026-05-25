"""Shared fixtures for the backend test suite.

Most tests want one of two things:

  - A lightweight stand-in for a `Device` row that exposes the attributes our
    pure helpers and classifier rules read (ha_state, source, licenses,
    current_version, etc.). We use `types.SimpleNamespace` rather than the
    real SQLAlchemy mapped class because (a) instantiating the mapped class
    requires a configured engine, and (b) the helpers only do attribute
    lookups — they never call ORM machinery.

  - A `DeviceUpgradeTask`-shaped object whose `.progress` dict round-trips
    through the phase-marker helpers, plus a Session double whose `commit()`
    is a no-op. SimpleNamespace handles both — `_mark_phase_done` mutates
    `task.progress` directly and then calls `db.commit()`, so a MagicMock
    Session is enough.

Keeping these as SimpleNamespace/MagicMock means tests are fast (no DB),
deterministic, and isolated from migration state. When we eventually need
real DB-touching tests (e.g. orchestrator phases that re-read a row after
commit), we'll add an in-memory SQLite fixture alongside these.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.enums import DeviceSource, HARole


def _device(**overrides) -> SimpleNamespace:
    """Build a Device-shaped object. Override only what the test cares about."""
    base = {
        "id": 1,
        "name": "fw-01",
        "hostname": "fw-01.example.com",
        "ip_address": "10.0.0.1",
        "serial": "0123456789",
        "source": DeviceSource.DIRECT,
        "ha_role": HARole.STANDALONE,
        "ha_state": None,
        "ha_peer_id": None,
        "current_version": "10.2.0",
        "model": "PA-440",
        "licenses": None,
        "verify_tls": True,
        "proxy_via_panorama": False,
        "panorama_id": None,
        "credential_id": None,
        "device_group": None,
        "template_stack": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def device():
    """A plain standalone, direct-attached device at 10.2.0."""
    return _device()


@pytest.fixture
def make_device():
    """Factory for tests that need several distinct device shapes."""
    return _device


@pytest.fixture
def fake_task():
    """A DeviceUpgradeTask-shaped object with a mutable progress dict.

    The phase-marker helpers read/write `task.progress["completed_phases"]`
    and call `db.commit()`. SimpleNamespace + MagicMock cover both surfaces.
    """
    return SimpleNamespace(id=1, progress={})


@pytest.fixture
def fake_db():
    """Session double whose commit() is a no-op but recorded."""
    return MagicMock()
