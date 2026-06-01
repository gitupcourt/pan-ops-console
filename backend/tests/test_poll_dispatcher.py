"""Tests for the #89 PR-3 staggered polling dispatcher.

Exercises the real `dispatch_due` / `poll_device_task` logic with the Redis
primitives and the device-poll core stubbed out (no broker, no PAN client):
due-selection, optimistic claim, per-Panorama cap, fair-share across
Panoramas, known-offline skip, and the success / failure-rewind paths.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone

import pytest

import app.capacity.tasks as tasks_mod
from app.capacity.tasks import _as_utc
from app.core.devices.models.device import Device
from app.core.devices.models.enums import DeviceSource


def test_concurrency_module_imports_and_pano_key_routing():
    """Import the lazily-loaded concurrency module (catches syntax/import
    errors the backend-image smoke wouldn't, since it's imported only at task
    runtime) and check pano_key_for routing — the pure, Redis-free part."""
    from app.core import concurrency

    direct = Device(name="x", hostname="x")  # no panorama linkage
    assert concurrency.pano_key_for(direct) == "direct"
    proxied = Device(
        name="y", hostname="y", proxy_via_panorama=True, panorama_id=7, serial="S1"
    )
    assert concurrency.pano_key_for(proxied) == "7"
    # proxy flag set but no serial → can't proxy → grouped as direct
    no_serial = Device(name="z", hostname="z", proxy_via_panorama=True, panorama_id=7)
    assert concurrency.pano_key_for(no_serial) == "direct"


def _mk(db, name, **kw):
    d = Device(name=name, hostname=kw.pop("hostname", name), **kw)
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


@contextlib.contextmanager
def _yield_true(*a, **k):
    yield True


@pytest.fixture
def dispatch_env(monkeypatch):
    """Stub the Redis primitives + task enqueue. Returns (enqueued, counts):
    `enqueued` is the list of apply_async args; `counts` the per-pano_key
    in-flight tally enforcing the cap."""
    enqueued: list = []
    counts: dict[str, int] = {}

    def fake_try_acquire(pano_key, token, cap, **kw):
        if counts.get(pano_key, 0) < cap:
            counts[pano_key] = counts.get(pano_key, 0) + 1
            return True
        return False

    monkeypatch.setattr("app.core.concurrency.dispatch_lock", _yield_true)
    monkeypatch.setattr("app.core.concurrency.try_acquire_slot", fake_try_acquire)
    monkeypatch.setattr(
        tasks_mod.poll_device_task, "apply_async",
        lambda **kw: enqueued.append(kw["args"]),
    )
    return enqueued, counts


def test_dispatch_enqueues_due_and_claims(client, db, dispatch_env):
    enqueued, _ = dispatch_env
    d1 = _mk(db, "d1")  # NULL timestamps → due for both classes
    d2 = _mk(db, "d2")

    tasks_mod.dispatch_due()

    ids = {args[0] for args in enqueued}
    assert ids == {d1.id, d2.id}
    # NULL → both classes due; args[1] carries the class list
    for args in enqueued:
        assert set(args[1]) == {"system", "config"}
    # Optimistic claim persisted both timestamps.
    db.expire_all()
    for did in (d1.id, d2.id):
        dev = db.get(Device, did)
        assert dev.last_system_poll_at is not None
        assert dev.last_config_poll_at is not None


def test_dispatch_respects_per_panorama_cap(client, db, dispatch_env):
    enqueued, _ = dispatch_env
    made = [_mk(db, f"c{i}") for i in range(5)]  # all "direct", all due

    # Default cap is 4 → only 4 enqueued this tick; the 5th stays unclaimed.
    tasks_mod.dispatch_due()

    assert len(enqueued) == 4
    db.expire_all()
    unclaimed = [m for m in made if db.get(Device, m.id).last_system_poll_at is None]
    assert len(unclaimed) == 1


def test_dispatch_skips_not_due(client, db, dispatch_env):
    enqueued, _ = dispatch_env
    now = datetime.now(timezone.utc)
    _mk(db, "fresh", last_system_poll_at=now, last_config_poll_at=now)

    tasks_mod.dispatch_due()
    assert enqueued == []


def test_dispatch_known_offline_claims_and_skips(client, db, dispatch_env):
    enqueued, _ = dispatch_env
    d = _mk(db, "offline", source=DeviceSource.PANORAMA, connected=False)

    tasks_mod.dispatch_due()

    # Not enqueued (no slot, no proxied round-trip)...
    assert enqueued == []
    # ...but claimed so it isn't re-checked every tick, with a skip note.
    db.expire_all()
    dev = db.get(Device, d.id)
    assert dev.last_system_poll_at is not None
    assert dev.last_poll_error and "disconnected" in dev.last_poll_error.lower()


def test_dispatch_fair_share_across_panoramas(client, db, dispatch_env, monkeypatch):
    enqueued, _ = dispatch_env
    # Group by first char of name → group "a" (3 devices), group "b" (1).
    monkeypatch.setattr("app.core.concurrency.pano_key_for", lambda d: d.name[0])
    for n in ("a1", "a2", "a3", "b1"):
        _mk(db, n)

    tasks_mod.dispatch_due()

    # Fair-share round-robin serves the lone "b" device EARLY (2nd enqueue),
    # not last — draining "a" first would push it to position 4.
    names = [db.get(Device, args[0]).name for args in enqueued]
    assert len(names) == 4
    assert names[1].startswith("b"), names


def test_poll_device_task_success_sets_class_timestamp(client, db, monkeypatch):
    released = []
    monkeypatch.setattr("app.capacity.services.poller.poll_device", lambda db_, d, m: [])
    monkeypatch.setattr("app.alerts.services.evaluator.evaluate_device_samples", lambda *a, **k: None)
    monkeypatch.setattr("app.core.concurrency.release_slot", lambda pk, tok: released.append((pk, tok)))
    d = _mk(db, "s1")

    tasks_mod.poll_device_task(d.id, ["system"], "direct", "tok")

    db.expire_all()
    dev = db.get(Device, d.id)
    assert dev.last_system_poll_at is not None
    assert dev.last_config_poll_at is None  # only the system class was polled
    assert dev.last_poll_at is not None
    assert dev.last_poll_error is None
    assert released == [("direct", "tok")]


def test_poll_device_task_failure_rewinds_for_near_term_retry(client, db, monkeypatch):
    released = []

    def boom(db_, d, m):
        raise ValueError("proxy exploded")

    monkeypatch.setattr("app.capacity.services.poller.poll_device", boom)
    monkeypatch.setattr("app.core.concurrency.release_slot", lambda pk, tok: released.append((pk, tok)))
    d = _mk(db, "f1")

    tasks_mod.poll_device_task(d.id, ["system"], "direct", "tok")

    db.expire_all()
    dev = db.get(Device, d.id)
    # Rewound to ~ now - (system_interval - backoff) = now - (300 - 60) = now-240,
    # so the dispatcher re-selects it in ~60s, not a full interval later.
    age = (datetime.now(timezone.utc) - _as_utc(dev.last_system_poll_at)).total_seconds()
    assert 200 < age < 280, age
    assert dev.last_poll_error and "proxy exploded" in dev.last_poll_error
    assert released == [("direct", "tok")]
