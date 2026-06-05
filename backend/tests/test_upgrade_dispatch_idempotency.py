"""Pass 1 hardening (from job 10): make drive_pair safe against duplicate
dispatches and re-entry.

  - Per-pair dispatch lock: a duplicate drive_pair dispatch for a (job, pair)
    already being driven is a NO-OP (operator clicking Proceed twice / on both
    HA members / a stray re-dispatch), not a second concurrent drive that races
    the first.
  - Stable HA roles: the passive/active assignment is persisted on first
    classify and reused, so a re-entry mid-upgrade (a member suspended/installing
    reads role "unknown") doesn't abort the pair with "Could not determine HA
    roles" — the exact failure that killed homegateway in job 10.
  - Install-step retry: the transient "A software install is in progress" error
    is retried, like downloads.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.devices.models.enums import HARole
from app.upgrade.services import upgrade


# ---------- per-pair dispatch lock ----------


def _lock_yielding(value):
    @contextlib.contextmanager
    def _cm(name, ttl=None):
        yield value
    return _cm


def test_duplicate_dispatch_is_a_noop_when_lock_held(monkeypatch):
    # Another invocation already holds the pair lock → dispatch_lock yields False.
    monkeypatch.setattr(upgrade, "dispatch_lock", _lock_yielding(False))
    calls = []
    monkeypatch.setattr(upgrade, "_drive_pair_inner", lambda *a: calls.append(a))
    upgrade.drive_pair(1, "pair-9")
    assert calls == []  # the duplicate did NOT drive the pair


def test_dispatch_drives_when_lock_acquired(monkeypatch):
    monkeypatch.setattr(upgrade, "dispatch_lock", _lock_yielding(True))
    calls = []
    monkeypatch.setattr(upgrade, "_drive_pair_inner", lambda *a: calls.append(a))
    upgrade.drive_pair(7, "pair-2")
    assert calls == [(7, "pair-2")]


# ---------- stable HA roles ----------


def _task(*, live_role, ha_member=None, tid=1):
    prog = {}
    if ha_member is not None:
        prog["ha_member"] = ha_member
    return SimpleNamespace(id=tid, progress=prog, device=SimpleNamespace(ha_role=live_role))


def test_classify_persists_roles_on_first_classify():
    db = MagicMock()
    passive_t = _task(live_role=HARole.PASSIVE, tid=1)
    active_t = _task(live_role=HARole.ACTIVE, tid=2)
    passive, active = upgrade._classify_pair(db, [passive_t, active_t])
    assert passive is passive_t and active is active_t
    assert passive_t.progress["ha_member"] == "passive"
    assert active_t.progress["ha_member"] == "active"


def test_classify_reuses_persisted_roles_when_live_role_is_unknown():
    # Re-entry mid-upgrade: a suspended/installing member now reads UNKNOWN, but
    # the assignment was persisted on the first classify — reuse it, don't abort.
    db = MagicMock()
    passive_t = _task(live_role=HARole.UNKNOWN, ha_member="passive", tid=1)
    active_t = _task(live_role=HARole.UNKNOWN, ha_member="active", tid=2)
    passive, active = upgrade._classify_pair(db, [passive_t, active_t])
    assert passive is passive_t and active is active_t  # persisted assignment used


def test_classify_unclear_on_first_pass_returns_none():
    # No persisted roles AND live roles genuinely unclear (no passive) → (None,..)
    # so the caller aborts — the existing, correct behavior for a truly
    # un-classifiable pair on its very first pass.
    db = MagicMock()
    a = _task(live_role=HARole.ACTIVE, tid=1)
    b = _task(live_role=HARole.UNKNOWN, tid=2)
    passive, active = upgrade._classify_pair(db, [a, b])
    assert passive is None


# ---------- install-step retry ----------


def test_is_transient_install_error():
    assert upgrade._is_transient_install_error(
        Exception("software install request failed: A software install is in progress. Please try again later")
    )
    assert upgrade._is_transient_install_error(Exception("install is in progress"))
    assert not upgrade._is_transient_install_error(Exception("not enough disk space"))


class _InstallClient:
    def __init__(self, fail_times=0, transient=True):
        self.attempts = 0
        self.fail_times = fail_times
        self.transient = transient

    def request_software_install(self, version):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            reason = (
                "A software install is in progress. Please try again later"
                if self.transient
                else "not enough disk space"
            )
            raise RuntimeError(f"software install request failed: {reason}")
        return "instjob-1"


@pytest.fixture
def install_io(monkeypatch):
    monkeypatch.setattr(upgrade.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(upgrade, "_wait_for_install_job", lambda *a, **k: True)
    monkeypatch.setattr(upgrade, "_record", lambda *a, **k: None)
    monkeypatch.setattr(upgrade, "_set_substep", lambda *a, **k: None)


def _dev():
    return SimpleNamespace(name="fw1")


def test_install_retries_transient_then_succeeds(install_io, monkeypatch):
    client = _InstallClient(fail_times=2, transient=True)
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: client)
    upgrade._install_with_retry(MagicMock(), SimpleNamespace(), _dev(), "12.1.7")
    assert client.attempts == 3  # two transient failures, third succeeded


def test_install_nontransient_raises_immediately(install_io, monkeypatch):
    client = _InstallClient(fail_times=1, transient=False)
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: client)
    with pytest.raises(Exception):
        upgrade._install_with_retry(MagicMock(), SimpleNamespace(), _dev(), "12.1.7")
    assert client.attempts == 1  # not retried — not transient


def test_install_exhausts_retries_and_raises(install_io, monkeypatch):
    client = _InstallClient(fail_times=99, transient=True)
    monkeypatch.setattr(upgrade, "_client_for", lambda db, d: client)
    with pytest.raises(Exception):
        upgrade._install_with_retry(MagicMock(), SimpleNamespace(), _dev(), "12.1.7")
    assert client.attempts == upgrade.INSTALL_RETRY_ATTEMPTS
