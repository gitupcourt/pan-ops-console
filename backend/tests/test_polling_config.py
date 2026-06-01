"""Tests for the #89 PR-4 runtime polling config + resolver.

Covers the admin GET/PATCH surface (seeded defaults, persistence, validation
bounds, auth gating), the resolver, and — the point of the whole PR — that
the dispatcher reads the DB config at runtime (a PATCHed per-Panorama cap is
honored on the next dispatch).
"""

from __future__ import annotations

import contextlib

STRONG = "correct horse battery staple"


def _admin(client):
    client.post("/auth/signup-first", json={"username": "admin", "password": STRONG})


def test_get_polling_config_returns_seeded_defaults(client):
    _admin(client)
    r = client.get("/settings/polling")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["system_interval_seconds"] == 300
    assert b["config_interval_seconds"] == 43200  # 12h
    assert b["max_concurrency_per_panorama"] == 4
    assert b["device_retry_backoff_seconds"] == 60
    assert b["dispatch_tick_seconds"] == 30  # info-only, from settings


def test_patch_polling_config_persists(client):
    _admin(client)
    r = client.patch(
        "/settings/polling",
        json={"config_interval_seconds": 21600, "max_concurrency_per_panorama": 8},
    )
    assert r.status_code == 200, r.text
    assert r.json()["config_interval_seconds"] == 21600
    assert r.json()["max_concurrency_per_panorama"] == 8
    # untouched field unchanged; change persisted across a fresh GET
    again = client.get("/settings/polling").json()
    assert again["config_interval_seconds"] == 21600
    assert again["system_interval_seconds"] == 300


def test_patch_polling_config_rejects_out_of_range(client):
    _admin(client)
    assert client.patch("/settings/polling", json={"system_interval_seconds": 5}).status_code == 422
    assert client.patch("/settings/polling", json={"max_concurrency_per_panorama": 999}).status_code == 422
    assert client.patch("/settings/polling", json={"config_interval_seconds": 60}).status_code == 422


def test_polling_config_requires_auth(client):
    _admin(client)
    client.post("/auth/logout")
    assert client.get("/settings/polling").status_code == 401


def test_resolver_returns_db_row(client, db):
    _admin(client)
    client.patch("/settings/polling", json={"system_interval_seconds": 120})
    from app.capacity.services.polling_config import get_polling_config

    db.expire_all()
    cfg = get_polling_config(db)
    assert cfg.system_interval_seconds == 120


def test_dispatcher_honors_db_cap(client, db, monkeypatch):
    """The dispatcher reads the per-Panorama cap from the DB each run: set
    cap=1, seed 3 due 'direct' devices → only 1 is enqueued this tick."""
    import app.capacity.tasks as tasks_mod
    from app.core.devices.models.device import Device

    _admin(client)
    client.patch("/settings/polling", json={"max_concurrency_per_panorama": 1})
    for n in ("p1", "p2", "p3"):
        db.add(Device(name=n, hostname=n))
    db.commit()

    counts: dict[str, int] = {}

    def fake_try(pano_key, token, cap, **kw):
        if counts.get(pano_key, 0) < cap:
            counts[pano_key] = counts.get(pano_key, 0) + 1
            return True
        return False

    @contextlib.contextmanager
    def yes(*a, **k):
        yield True

    enq: list = []
    monkeypatch.setattr("app.core.concurrency.dispatch_lock", yes)
    monkeypatch.setattr("app.core.concurrency.try_acquire_slot", fake_try)
    monkeypatch.setattr(tasks_mod.poll_device_task, "apply_async", lambda **kw: enq.append(kw["args"]))

    tasks_mod.dispatch_due()
    assert len(enq) == 1, enq  # cap=1 from the DB row was honored
