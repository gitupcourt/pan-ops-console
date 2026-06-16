"""retry_on_transient + is_transient_device_error — resilience for read-only
device ops against a momentarily-slow management plane.

Job 18 exposed the gap: a precheck and a post-upgrade snapshot both timed out on
a box that was merely busy/slow right after its reboot, and each gave up on the
first try (the precheck errored; the snapshot persisted empty, so the compare
then failed with "either side is None"). Those ops are read-only, so we now retry
transient timeouts before failing — but NOT permanent errors, and never writes.
"""

from __future__ import annotations

import pytest

from app.core.command_proxy import pan_client


# ---------- is_transient_device_error ----------


def test_transient_true_for_timeouts_and_drops():
    for m in [
        "The read operation timed out",
        "HTTPSConnectionPool(...): Read timed out. (read timeout=30)",
        "Connection reset by peer",
        "RemoteDisconnected: Remote end closed connection without response",
        "Target disconnected",
    ]:
        assert pan_client.is_transient_device_error(Exception(m)), m


def test_transient_false_for_permanent():
    for m in [
        "Connection refused",
        "Name or service not known",
        "401 Unauthorized: invalid API key",
        "no direct route to device",
    ]:
        assert not pan_client.is_transient_device_error(Exception(m)), m


# ---------- retry_on_transient ----------


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # Don't actually wait between retries in tests.
    monkeypatch.setattr(pan_client.time, "sleep", lambda *_a, **_k: None)


def test_retry_returns_on_first_success():
    calls = {"n": 0}

    def ok():
        calls["n"] += 1
        return "result"

    assert pan_client.retry_on_transient(ok, attempts=3) == "result"
    assert calls["n"] == 1  # no retries when it works first time


def test_retry_recovers_after_transient_blips():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("read operation timed out")
        return "ok"

    assert pan_client.retry_on_transient(flaky, attempts=3) == "ok"
    assert calls["n"] == 3  # two transient blips, third attempt succeeds


def test_retry_does_not_retry_permanent_errors():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise ConnectionError("Connection refused")

    with pytest.raises(ConnectionError):
        pan_client.retry_on_transient(boom, attempts=3)
    assert calls["n"] == 1  # permanent -> fail fast, no retry


def test_retry_exhausts_then_reraises():
    calls = {"n": 0}

    def always_slow():
        calls["n"] += 1
        raise TimeoutError("timed out")

    with pytest.raises(TimeoutError):
        pan_client.retry_on_transient(always_slow, attempts=3)
    assert calls["n"] == 3  # tried the whole budget, then gave up
