"""Tests for the upgrade module's HTTP surface — jobs + tasks + images.

Covers the operator-facing route contract from phase 4c-routes:

  - Job creation: device validation, image-source validation,
    workflow-stages validation, HA pair key derivation.
  - Job list: returns task_count aggregate.
  - Job lifecycle: start (PENDING→RUNNING), abort (RUNNING→ABORTED),
    delete (gated by state).
  - Task ops: confirm + override are token-gated and phase-gated.
    Retry clears error and resets phase without wiping `progress`.

The orchestrator itself isn't exercised here — phase 4d wires the
Celery dispatch and that's where end-to-end runs land. These tests
pin the API contract that 4d and the frontend depend on.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.devices.models.device import Device
from app.core.devices.models.enums import DeviceSource, HARole
from app.upgrade.models.enums import JobState, TaskPhase
from app.upgrade.models.image import PanosImage
from app.upgrade.models.job import DeviceUpgradeTask, UpgradeJob


STRONG = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _stub_celery_dispatch(monkeypatch):
    """Stub `drive_pair_task.delay` so route tests don't try to hit Redis.

    Phase 4d wired the routes to dispatch the orchestrator via Celery
    on /start, /confirm, /override, and /retry. In tests there's no
    broker, so the real `.delay()` call would error out trying to
    connect. We replace it with a no-op that returns a fake AsyncResult
    shape (good enough for the routes' return path — none of them
    inspect the result).

    Tests that care about WHICH calls were made should use a different
    fixture (or override this one) that records args. The default here
    is "tests pass without exercising the broker."
    """
    from app.upgrade.routes import jobs as jobs_routes

    class _FakeAsyncResult:
        id = "fake-task-id"

    def _fake_delay(*args, **kwargs):
        return _FakeAsyncResult()

    monkeypatch.setattr(
        jobs_routes.drive_pair_task, "delay", _fake_delay
    )
    yield


def _signup(client):
    return client.post(
        "/auth/signup-first",
        json={"username": "admin", "password": STRONG},
    )


def _seed_standalone(db, *, name: str, ip: str = "10.0.0.1") -> Device:
    """A device with no HA peer — solo task in the upgrade orchestrator."""
    dev = Device(
        name=name,
        hostname=f"{name}.local",
        ip_address=ip,
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


def _seed_ha_pair(db, *, name_a: str, name_b: str) -> tuple[Device, Device]:
    """Two devices peered via ha_peer_id — orchestrator serializes them."""
    a = Device(
        name=name_a,
        hostname=f"{name_a}.local",
        verify_tls=False,
        proxy_via_panorama=False,
        polling_enabled=True,
        source=DeviceSource.DIRECT,
        ha_role=HARole.ACTIVE,
    )
    b = Device(
        name=name_b,
        hostname=f"{name_b}.local",
        verify_tls=False,
        proxy_via_panorama=False,
        polling_enabled=True,
        source=DeviceSource.DIRECT,
        ha_role=HARole.PASSIVE,
    )
    db.add_all([a, b])
    db.commit()
    db.refresh(a)
    db.refresh(b)
    # Link peers — self-FK with post_update lets either order land.
    a.ha_peer_id = b.id
    b.ha_peer_id = a.id
    db.commit()
    db.refresh(a)
    db.refresh(b)
    return a, b


def _seed_image(db, version: str = "11.1.4-h7") -> PanosImage:
    img = PanosImage(version=version)
    db.add(img)
    db.commit()
    db.refresh(img)
    return img


# ---------- /upgrade/jobs creation ----------


def test_create_job_with_device_pull_succeeds(client, db):
    _signup(client)
    dev = _seed_standalone(db, name="fw1")

    r = client.post(
        "/upgrade/jobs",
        json={
            "name": "test-run",
            "target_version": "11.1.4-h7",
            "device_ids": [dev.id],
            "device_pull_image": True,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == JobState.PENDING.value
    assert body["task_count"] == 1
    assert body["device_pull_image"] is True
    assert body["image_id"] is None
    assert len(body["tasks"]) == 1
    assert body["tasks"][0]["device_id"] == dev.id
    assert body["tasks"][0]["phase"] == TaskPhase.PENDING.value


def test_create_job_with_image_id_succeeds(client, db):
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    img = _seed_image(db)

    r = client.post(
        "/upgrade/jobs",
        json={
            "name": "test-run",
            "target_version": img.version,
            "device_ids": [dev.id],
            "image_id": img.id,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["image_id"] == img.id


def test_create_job_requires_an_image_source(client, db):
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    r = client.post(
        "/upgrade/jobs",
        json={
            "name": "test-run",
            "target_version": "11.1.4-h7",
            "device_ids": [dev.id],
        },
    )
    # Either image_id or device_pull_image must be set — model_validator
    # rejects with 422 (Pydantic validation).
    assert r.status_code == 422
    assert "image_id" in r.text and "device_pull_image" in r.text


def test_create_job_rejects_both_image_sources(client, db):
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    img = _seed_image(db)
    r = client.post(
        "/upgrade/jobs",
        json={
            "name": "test-run",
            "target_version": img.version,
            "device_ids": [dev.id],
            "image_id": img.id,
            "device_pull_image": True,
        },
    )
    assert r.status_code == 422


def test_create_job_with_missing_device_ids_400s(client, db):
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    r = client.post(
        "/upgrade/jobs",
        json={
            "name": "test-run",
            "target_version": "11.1.4-h7",
            "device_ids": [dev.id, 9999, 8888],
            "device_pull_image": True,
        },
    )
    # 400 with the missing IDs in the detail so the operator can fix
    # the request rather than guess.
    assert r.status_code == 400
    assert "9999" in r.text and "8888" in r.text


def test_create_job_with_unknown_image_id_400s(client, db):
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    r = client.post(
        "/upgrade/jobs",
        json={
            "name": "test-run",
            "target_version": "11.1.4-h7",
            "device_ids": [dev.id],
            "image_id": 9999,
        },
    )
    assert r.status_code == 400


def test_create_job_partial_workflow_requires_stages(client, db):
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    r = client.post(
        "/upgrade/jobs",
        json={
            "name": "test-run",
            "target_version": "11.1.4-h7",
            "device_ids": [dev.id],
            "device_pull_image": True,
            "workflow": "partial",
        },
    )
    # PARTIAL workflow without stages — model_validator rejects.
    assert r.status_code == 422


def test_create_job_partial_workflow_validates_stage_names(client, db):
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    r = client.post(
        "/upgrade/jobs",
        json={
            "name": "test-run",
            "target_version": "11.1.4-h7",
            "device_ids": [dev.id],
            "device_pull_image": True,
            "workflow": "partial",
            "workflow_stages": ["precheck", "not_a_real_phase"],
        },
    )
    assert r.status_code == 422
    assert "not_a_real_phase" in r.text


def test_create_job_with_ha_pair_groups_tasks_with_same_key(client, db):
    _signup(client)
    a, b = _seed_ha_pair(db, name_a="fwA", name_b="fwB")
    r = client.post(
        "/upgrade/jobs",
        json={
            "name": "ha-test",
            "target_version": "11.1.4-h7",
            "device_ids": [a.id, b.id],
            "device_pull_image": True,
        },
    )
    assert r.status_code == 201
    tasks = r.json()["tasks"]
    assert len(tasks) == 2
    # Both tasks share the same ha_pair_key — derived from min(id, peer.id).
    keys = {t["ha_pair_key"] for t in tasks}
    assert len(keys) == 1, f"ha-paired tasks should share key: got {keys}"
    # Key shape: "pair-<min_id>"
    assert next(iter(keys)) == f"pair-{min(a.id, b.id)}"


def test_create_job_standalone_device_gets_unique_key(client, db):
    _signup(client)
    dev = _seed_standalone(db, name="solo")
    r = client.post(
        "/upgrade/jobs",
        json={
            "name": "solo-test",
            "target_version": "11.1.4-h7",
            "device_ids": [dev.id],
            "device_pull_image": True,
        },
    )
    # Standalone device gets a non-paired key (its own id, no "pair-" prefix).
    assert r.json()["tasks"][0]["ha_pair_key"] == str(dev.id)


# ---------- /upgrade/jobs list + get ----------


def test_list_jobs_returns_task_count(client, db):
    _signup(client)
    a, b = _seed_ha_pair(db, name_a="A", name_b="B")
    c = _seed_standalone(db, name="C", ip="10.0.0.3")

    client.post("/upgrade/jobs", json={
        "name": "job1", "target_version": "11.1.4-h7",
        "device_ids": [a.id, b.id], "device_pull_image": True,
    })
    client.post("/upgrade/jobs", json={
        "name": "job2", "target_version": "11.1.4-h7",
        "device_ids": [c.id], "device_pull_image": True,
    })

    r = client.get("/upgrade/jobs")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    # Newest first.
    assert body[0]["name"] == "job2"
    assert body[0]["task_count"] == 1
    assert body[1]["name"] == "job1"
    assert body[1]["task_count"] == 2


def test_get_unknown_job_404s(client):
    _signup(client)
    r = client.get("/upgrade/jobs/9999")
    assert r.status_code == 404


# ---------- /upgrade/jobs lifecycle ----------


def test_start_job_transitions_pending_to_running(client, db):
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    job_id = create.json()["id"]

    r = client.post(f"/upgrade/jobs/{job_id}/start")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == JobState.RUNNING.value
    assert body["started_at"] is not None


def test_start_already_running_is_idempotent(client, db):
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    job_id = create.json()["id"]
    client.post(f"/upgrade/jobs/{job_id}/start")
    # Second start: also 200, no error.
    r = client.post(f"/upgrade/jobs/{job_id}/start")
    assert r.status_code == 200


def test_start_aborted_job_409s(client, db):
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    job_id = create.json()["id"]
    client.post(f"/upgrade/jobs/{job_id}/start")
    client.post(f"/upgrade/jobs/{job_id}/abort")
    r = client.post(f"/upgrade/jobs/{job_id}/start")
    assert r.status_code == 409


def test_abort_running_job(client, db):
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    job_id = create.json()["id"]
    client.post(f"/upgrade/jobs/{job_id}/start")
    r = client.post(f"/upgrade/jobs/{job_id}/abort")
    assert r.status_code == 200
    assert r.json()["state"] == JobState.ABORTED.value
    assert r.json()["finished_at"] is not None


def test_abort_already_terminal_409s(client, db):
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    job_id = create.json()["id"]
    client.post(f"/upgrade/jobs/{job_id}/start")
    client.post(f"/upgrade/jobs/{job_id}/abort")
    r = client.post(f"/upgrade/jobs/{job_id}/abort")
    assert r.status_code == 409


def test_delete_running_job_refused(client, db):
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    job_id = create.json()["id"]
    client.post(f"/upgrade/jobs/{job_id}/start")
    r = client.delete(f"/upgrade/jobs/{job_id}")
    assert r.status_code == 409


def test_delete_pending_job_cascades_to_tasks(client, db):
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    job_id = create.json()["id"]
    task_id = create.json()["tasks"][0]["id"]

    r = client.delete(f"/upgrade/jobs/{job_id}")
    assert r.status_code == 204

    # Both job and its task are gone.
    assert db.get(UpgradeJob, job_id) is None
    assert db.get(DeviceUpgradeTask, task_id) is None


# ---------- /upgrade/tasks ops ----------


def test_task_confirm_requires_parked_phase(client, db):
    """A task in PENDING isn't ready for confirm — needs an AWAITING_*_CONFIRM phase."""
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    task_id = create.json()["tasks"][0]["id"]

    r = client.post(f"/upgrade/tasks/{task_id}/confirm")
    assert r.status_code == 409


def test_task_confirm_signals_advance_when_parked(client, db):
    """When the task is parked at an AWAITING_*_CONFIRM phase, hitting
    /confirm sets a non-empty confirmation_token so the orchestrator's
    `_wait_for_confirm` poll loop picks it up on the next tick and
    proceeds. No request body required — auth via session cookie is
    the access-control gate.
    """
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    task_id = create.json()["tasks"][0]["id"]

    # Park the task at AWAITING_REBOOT_CONFIRM. No pre-set token — the
    # orchestrator never sets one; only the route does.
    task = db.get(DeviceUpgradeTask, task_id)
    task.phase = TaskPhase.AWAITING_REBOOT_CONFIRM
    db.commit()

    r = client.post(f"/upgrade/tasks/{task_id}/confirm")
    assert r.status_code == 200
    db.refresh(task)
    assert task.confirmation_token  # non-empty sentinel set


def test_task_override_works_on_precheck_override_phase(client, db):
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    task_id = create.json()["tasks"][0]["id"]
    task = db.get(DeviceUpgradeTask, task_id)
    task.phase = TaskPhase.AWAITING_PRECHECK_OVERRIDE
    db.commit()

    # Confirm endpoint refuses override phases — must use /override.
    r = client.post(f"/upgrade/tasks/{task_id}/confirm")
    assert r.status_code == 409

    r = client.post(f"/upgrade/tasks/{task_id}/override")
    assert r.status_code == 200
    db.refresh(task)
    assert task.confirmation_token  # non-empty sentinel set


def test_task_override_records_audit_entry(client, db):
    """Override records who clicked it in progress.overrides + log so the
    JobDetail panel can show 'overridden by USERNAME' even after the
    task has moved past the gate."""
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    task_id = create.json()["tasks"][0]["id"]
    task = db.get(DeviceUpgradeTask, task_id)
    task.phase = TaskPhase.AWAITING_PRECHECK_OVERRIDE
    db.commit()

    r = client.post(f"/upgrade/tasks/{task_id}/override")
    assert r.status_code == 200
    db.refresh(task)
    progress = task.progress or {}
    overrides = progress.get("overrides") or []
    assert len(overrides) == 1
    o = overrides[0]
    assert o["by"] == "admin"
    assert o["phase"] == TaskPhase.AWAITING_PRECHECK_OVERRIDE.value
    assert "at" in o
    log = progress.get("log") or []
    assert any("Override" in line and "admin" in line for line in log)


def test_task_rerun_check_sets_rerun_token_and_audits(client, db):
    """Re-run check sets a RERUN_-prefixed token (so the orchestrator's
    _wait_for_override routes to its RERUN branch) AND records the
    operator on the activity log."""
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    task_id = create.json()["tasks"][0]["id"]
    task = db.get(DeviceUpgradeTask, task_id)
    task.phase = TaskPhase.AWAITING_PRECHECK_OVERRIDE
    db.commit()

    r = client.post(f"/upgrade/tasks/{task_id}/rerun-check")
    assert r.status_code == 200
    db.refresh(task)
    assert task.confirmation_token
    assert task.confirmation_token.startswith("RERUN_")
    log = (task.progress or {}).get("log") or []
    assert any(
        "Re-run check" in line and "admin" in line for line in log
    )


def test_task_rerun_check_refuses_non_override_phase(client, db):
    """Re-run is only meaningful at the precheck/postcheck override
    gates; refuse at any other phase rather than silently no-op."""
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    task_id = create.json()["tasks"][0]["id"]
    # Task is PENDING by default — not an override gate.

    r = client.post(f"/upgrade/tasks/{task_id}/rerun-check")
    assert r.status_code == 409


def test_task_retry_clears_error_and_resets_phase(client, db):
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    task_id = create.json()["tasks"][0]["id"]

    # Simulate a failed task with progress + error.
    task = db.get(DeviceUpgradeTask, task_id)
    task.phase = TaskPhase.FAILED
    task.error = "panorama timeout during install"
    task.progress = {"completed_phases": ["precheck", "snapshot"]}
    db.commit()

    r = client.post(f"/upgrade/tasks/{task_id}/retry")
    assert r.status_code == 200
    db.refresh(task)
    assert task.phase == TaskPhase.PENDING
    assert task.error is None
    # progress.completed_phases is PRESERVED — that's what makes retry
    # resume from precheck/snapshot done instead of starting over.
    assert task.progress == {"completed_phases": ["precheck", "snapshot"]}


def test_task_retry_refuses_done_task(client, db):
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    task_id = create.json()["tasks"][0]["id"]
    task = db.get(DeviceUpgradeTask, task_id)
    task.phase = TaskPhase.DONE
    db.commit()

    r = client.post(f"/upgrade/tasks/{task_id}/retry")
    assert r.status_code == 409


def test_task_retry_refuses_parked_task(client, db):
    """Retry isn't the right tool for a parked task — operator should
    use /confirm or /override. Refuse loudly rather than silently
    convert a confirmation-needed task to PENDING."""
    _signup(client)
    dev = _seed_standalone(db, name="fw1")
    create = client.post("/upgrade/jobs", json={
        "name": "j", "target_version": "11.1.4-h7",
        "device_ids": [dev.id], "device_pull_image": True,
    })
    task_id = create.json()["tasks"][0]["id"]
    task = db.get(DeviceUpgradeTask, task_id)
    task.phase = TaskPhase.AWAITING_REBOOT_CONFIRM
    db.commit()

    r = client.post(f"/upgrade/tasks/{task_id}/retry")
    assert r.status_code == 409
    assert "/confirm" in r.text


# ---------- /upgrade/images ----------


def test_register_image_succeeds(client):
    _signup(client)
    r = client.post(
        "/upgrade/images",
        json={"version": "11.1.4-h7", "notes": "Q1 fleet target"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["version"] == "11.1.4-h7"
    assert body["uploaded"] is False
    assert body["filename"] is None


def test_list_images(client):
    _signup(client)
    client.post("/upgrade/images", json={"version": "11.1.4-h7"})
    client.post("/upgrade/images", json={"version": "11.0.0"})
    r = client.get("/upgrade/images")
    assert r.status_code == 200
    versions = [i["version"] for i in r.json()]
    # Sorted by version desc (string sort — h7 lexically > 0).
    assert versions == sorted(versions, reverse=True)


def test_delete_image(client):
    _signup(client)
    create = client.post("/upgrade/images", json={"version": "11.1.4-h7"})
    image_id = create.json()["id"]
    r = client.delete(f"/upgrade/images/{image_id}")
    assert r.status_code == 204
    r = client.delete(f"/upgrade/images/{image_id}")
    assert r.status_code == 404


# ---------- auth ----------


def test_upgrade_routes_require_auth(client):
    """Sanity: no session cookie → 401 on every upgrade route."""
    # Don't signup.
    assert client.get("/upgrade/jobs").status_code == 401
    assert client.get("/upgrade/images").status_code == 401
    assert client.post(
        "/upgrade/jobs",
        json={
            "name": "x", "target_version": "11.1.4-h7",
            "device_ids": [1], "device_pull_image": True,
        },
    ).status_code == 401
