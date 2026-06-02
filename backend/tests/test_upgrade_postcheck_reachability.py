"""Post-reboot reconnect-wait before the postcheck (job #4 follow-up).

Job #4: after the upgrade reboot the device's own mgmt plane was back, but
the managing Panorama hadn't re-established its connection to it yet. The
postcheck runs through that (Panorama-proxied) route, so it fired into a
"Panorama unreachable" runner error and validated nothing — even though the
post_snapshot moments later succeeded because the route had recovered by
then. `_wait_for_upgrade_route_ready` polls the route until a light op
answers, so the postcheck runs against a reachable device.

These cover the polling helpers without real sleeps or a live firewall:
`_client_for` and the `time` module reference inside upgrade.py are both
swapped out.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.upgrade.services import upgrade


class _OkClient:
    def get_system_info(self):
        return {"sw-version": "12.1.4-h6"}


class _DeadClient:
    def get_system_info(self):
        raise ConnectionError("Panorama unreachable: the read operation timed out")


class _FakeClock:
    """Stand-in for the `time` module inside upgrade.py: monotonic() reads a
    virtual clock that sleep() advances, so timeout logic runs instantly and
    we can assert exactly how long the helper would have slept."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


_DEV = SimpleNamespace(name="fw1")


# ---------- _upgrade_route_reachable (one-shot probe) ----------

def test_route_reachable_true_when_probe_succeeds(monkeypatch):
    monkeypatch.setattr(upgrade, "_client_for", lambda db, device: _OkClient())
    assert upgrade._upgrade_route_reachable(None, _DEV) is True


def test_route_reachable_false_when_probe_raises(monkeypatch):
    monkeypatch.setattr(upgrade, "_client_for", lambda db, device: _DeadClient())
    assert upgrade._upgrade_route_reachable(None, _DEV) is False


def test_route_reachable_false_when_build_raises(monkeypatch):
    def _boom(db, device):
        raise ConnectionError("Panorama unreachable and no direct route available for fw1")

    monkeypatch.setattr(upgrade, "_client_for", _boom)
    assert upgrade._upgrade_route_reachable(None, _DEV) is False


# ---------- _wait_for_upgrade_route_ready (polling) ----------

def test_wait_returns_immediately_when_reachable(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(upgrade, "time", clock)
    monkeypatch.setattr(upgrade, "_upgrade_route_reachable", lambda db, device: True)

    assert (
        upgrade._wait_for_upgrade_route_ready(
            None, _DEV, timeout_s=300, poll_interval_s=20
        )
        is True
    )
    assert clock.sleeps == []  # happy path: probed once, never waited


def test_wait_returns_true_after_a_few_retries(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(upgrade, "time", clock)
    results = iter([False, False, True])
    monkeypatch.setattr(
        upgrade, "_upgrade_route_reachable", lambda db, device: next(results)
    )

    assert (
        upgrade._wait_for_upgrade_route_ready(
            None, _DEV, timeout_s=300, poll_interval_s=20
        )
        is True
    )
    # Slept twice (between the three probes), then the third probe answered.
    assert clock.sleeps == [20, 20]


def test_wait_times_out_when_never_reachable(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(upgrade, "time", clock)
    calls = {"n": 0}

    def _never(db, device):
        calls["n"] += 1
        return False

    monkeypatch.setattr(upgrade, "_upgrade_route_reachable", _never)

    assert (
        upgrade._wait_for_upgrade_route_ready(
            None, _DEV, timeout_s=60, poll_interval_s=20
        )
        is False
    )
    # Made at least one probe and stopped once the virtual clock passed the
    # deadline (proceeding anyway is the caller's job — the wait only helps).
    assert calls["n"] >= 1
