"""Tests for phase-4d Celery dispatch wiring.

Verifies that `/start`, `/confirm`, `/override`, and `/retry` send the
right `drive_pair_task.delay(job_id, ha_pair_key)` arguments to Celery.

These are surface-level — we don't actually run the orchestrator from
these tests (that's a separate concern handled by the orchestrator's
own unit tests + a future end-to-end harness). All we care about here
is: did the route fire the dispatch, with the right args, exactly the
right number of times?

The autouse fixture replaces `drive_pair_task.delay` with a recording
no-op so the dispatch is observable without a live broker.
"""

from __future__ import annotations

import pytest

from app.core.devices.models.device import Device
from app.core.devices.models.enums import DeviceSource, HARole
from app.upgrade.models.enums import TaskPhase
from app.upgrade.models.job import DeviceUpgradeTask


STRONG = "correct horse battery staple"


@pytest.fixture
def dispatched(monkeypatch):
    """Replace drive_pair_task.delay with a recorder.

    Returns a list that accumulates (args, kwargs) for every call —
    tests assert against it directly. Different from the autouse stub
    in test_upgrade_routes_jobs.py because this one OBSERVES; that one
    just swallows.
    """
    from app.upgrade.routes import jobs as jobs_routes

    calls: list[tuple] = []

    class _FakeAsyncResult:
        id = "fake-task-id"

    def _recording_delay(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakeAsyncResult()

    monkeypatch.setattr(
        jobs_routes.drive_pair_task, "delay", _recording_delay
    )
    return calls


def _signup(client):
    return client.post(
        "/auth/signup-first",
        json={"username": "admin", "password": STRONG},
    )


def _seed_standalone(db, *, name: str) -> Device:
    dev = Device(
        name=name,
        hostname=f"{name}.local",
        verify_tls=False,
        proxy_via_panorama=False,
        polling_enabled=True,
        source=DeviceSource.DIRECT,
        ha_role=HARole.STANDALONE,
    )
    db.add(dev)
    db.commit()
    db.refresh(dev)
    return dev


def _seed_ha_pair(db, *, name_a: str, name_b: str):
    a = Device(
        name=name_a, hostname=f"{name_a}.local", verify_tls=False,
        proxy_via_panorama=False, polling_enabled=True,
        source=DeviceSource.DIRECT, ha_role=HARole.ACTIVE,
    )
    b = Device(
        name=name_b, hostname=f"{name_b}.local", verify_tls=False,
        proxy_via_panorama=False, polling_enabled=True,
        source=DeviceSource.DIRECT, ha_role=HARole.PASSIVE,
    )
    db.add_all([a, b])
    db.commit()
    db.refresh(a)
    db.refresh(b)
    a.ha_peer_id = b.id
    b.ha_peer_id = a.id
    db.commit()
    db.refresh(a)
    db.refresh(b)
    return a, b


# ---------- /start dispatch ----------


def test_start_dispatches_once_per_ha_pair_key(client, db, dispatched):
    """One standalone + one HA pair = 2 unique ha_pair_keys = 2 dispatches.

    The orchestrator runs ONE drive_pair per pair (it manages both
    members itself), so the route must dedupe by ha_pair_key. Without
    dedupe an HA-paired job would dispatch twice for the same pair,
    causing duplicate orchestrator runs that race each other.
    """
    _signup(client)
    a, b = _seed_ha_pair(db, name_a="A", name_b="B")
    c = _seed_standalone(db, name="C")

    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [a.id, b.id, c.id],
        "device_pull_image": True,
    })
    job_id = create.json()["id"]

    r = client.post(f"/upgrade/jobs/{job_id}/start")
    assert r.status_code == 200

    # 3 devices → 2 unique ha_pair_keys (pair + solo) → 2 dispatches.
    assert len(dispatched) == 2

    keys_dispatched = {args[1] for args, _ in dispatched}
    expected_pair_key = f"pair-{min(a.id, b.id)}"
    expected_solo_key = str(c.id)
    assert keys_dispatched == {expected_pair_key, expected_solo_key}

    # Every dispatch carries the right job_id.
    for args, _ in dispatched:
        assert args[0] == job_id


def test_start_idempotent_does_not_re_dispatch(client, db, dispatched):
    """Calling /start on an already-RUNNING job is a no-op — must NOT
    re-dispatch the orchestrator (would duplicate the running pair tasks).
    """
    _signup(client)
    dev = _seed_standalone(db, name="fw")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    job_id = create.json()["id"]

    client.post(f"/upgrade/jobs/{job_id}/start")
    assert len(dispatched) == 1

    # Second /start: no new dispatch.
    r = client.post(f"/upgrade/jobs/{job_id}/start")
    assert r.status_code == 200
    assert len(dispatched) == 1, (
        "Idempotent /start dispatched again, would duplicate "
        "orchestrator runs"
    )


# ---------- /confirm dispatch ----------


def test_confirm_dispatches_for_parked_pair(client, db, dispatched):
    _signup(client)
    a, b = _seed_ha_pair(db, name_a="A", name_b="B")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [a.id, b.id], "device_pull_image": True,
    })
    task_id = create.json()["tasks"][0]["id"]
    job_id = create.json()["id"]

    # Park the task at a confirm gate.
    task = db.get(DeviceUpgradeTask, task_id)
    task.phase = TaskPhase.AWAITING_REBOOT_CONFIRM
    task.confirmation_token = "tok"
    db.commit()

    # /start already dispatched once on job creation? No — only /start.
    # Clear before testing /confirm in isolation.
    dispatched.clear()

    r = client.post(
        f"/upgrade/tasks/{task_id}/confirm", json={"token": "tok"}
    )
    assert r.status_code == 200

    # Confirm fires exactly one dispatch for THIS task's pair.
    assert len(dispatched) == 1
    assert dispatched[0][0] == (job_id, task.ha_pair_key)


# ---------- /override dispatch ----------


def test_override_dispatches_for_parked_task(client, db, dispatched):
    _signup(client)
    dev = _seed_standalone(db, name="fw")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    task_id = create.json()["tasks"][0]["id"]
    job_id = create.json()["id"]

    task = db.get(DeviceUpgradeTask, task_id)
    task.phase = TaskPhase.AWAITING_PRECHECK_OVERRIDE
    task.confirmation_token = "tok"
    db.commit()

    dispatched.clear()
    r = client.post(
        f"/upgrade/tasks/{task_id}/override", json={"token": "tok"}
    )
    assert r.status_code == 200
    assert len(dispatched) == 1
    assert dispatched[0][0] == (job_id, task.ha_pair_key)


# ---------- /retry dispatch ----------


def test_retry_dispatches_for_failed_task(client, db, dispatched):
    _signup(client)
    dev = _seed_standalone(db, name="fw")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    task_id = create.json()["tasks"][0]["id"]
    job_id = create.json()["id"]

    task = db.get(DeviceUpgradeTask, task_id)
    task.phase = TaskPhase.FAILED
    task.error = "transient"
    db.commit()

    dispatched.clear()
    r = client.post(f"/upgrade/tasks/{task_id}/retry")
    assert r.status_code == 200
    assert len(dispatched) == 1
    assert dispatched[0][0] == (job_id, task.ha_pair_key)


def test_failed_retry_calls_do_not_dispatch(client, db, dispatched):
    """If the route refuses the request (DONE / parked), no dispatch fires.

    Regression guard for the obvious failure mode: an over-eager
    dispatch inside the error path would still fire the orchestrator,
    which is the wrong semantic.
    """
    _signup(client)
    dev = _seed_standalone(db, name="fw")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    task_id = create.json()["tasks"][0]["id"]

    # Already DONE → 409 from /retry.
    task = db.get(DeviceUpgradeTask, task_id)
    task.phase = TaskPhase.DONE
    db.commit()
    dispatched.clear()
    r = client.post(f"/upgrade/tasks/{task_id}/retry")
    assert r.status_code == 409
    assert len(dispatched) == 0

    # Parked → 409 from /retry (operator should use /confirm).
    task.phase = TaskPhase.AWAITING_REBOOT_CONFIRM
    task.confirmation_token = "tok"
    db.commit()
    dispatched.clear()
    r = client.post(f"/upgrade/tasks/{task_id}/retry")
    assert r.status_code == 409
    assert len(dispatched) == 0


# ---------- celery task registration ----------


def test_drive_pair_task_is_registered_by_name():
    """The route dispatches `drive_pair_task.delay(...)` directly, so the
    Python import + decorator handle name registration. This test just
    pins the name string in case someone refactors `@celery.task(name=...)`
    — the orchestrator's beat / external dispatches use the string.
    """
    import app.upgrade.tasks  # noqa: F401
    from app.workers.celery_app import celery

    assert "upgrade.drive_pair" in celery.tasks, (
        f"upgrade.drive_pair not registered. Available: "
        f"{[n for n in celery.tasks if not n.startswith('celery.')]}"
    )
