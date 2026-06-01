"""Smoke tests for the Celery app + task registration.

These verify the wiring without actually running tasks — no broker
connection happens, no worker is started. Future refactors that
accidentally orphan a @celery.task definition (typo in include list,
moved module without updating, etc.) get caught here at CI time.
"""

from __future__ import annotations


def test_celery_app_loads():
    """Importing the Celery app should not require a live broker."""
    from app.workers.celery_app import celery

    assert celery.main == "panops"
    assert celery.conf.task_serializer == "json"
    assert celery.conf.timezone == "UTC"
    assert celery.conf.task_track_started is True
    # 1-day result expiry — load-bearing for long-running tasks in phase 4.
    assert celery.conf.result_expires == 86400


def test_capacity_poll_tasks_registered():
    """The capacity poller's Celery tasks must be discoverable by name.

    #89 PR-3 replaced the two "sweep" beats with a staggered dispatcher:
    `capacity.dispatch_due` (beat) enqueues `capacity.poll_device_task` per
    due device. `capacity.poll_all` stays for the manual "poll now" route.
    """
    # Importing the tasks module is what registers the @celery.task
    # decorator. The worker `include` list does this implicitly at boot;
    # the test does it explicitly so registration is observable.
    import app.capacity.tasks  # noqa: F401
    from app.workers.celery_app import celery

    available = [name for name in celery.tasks if not name.startswith("celery.")]
    for name in (
        "capacity.poll_all",
        "capacity.dispatch_due",
        "capacity.poll_device_task",
    ):
        assert name in celery.tasks, f"{name} not registered. Available: {available}"


def test_capacity_dispatch_beat_entry():
    """#89 PR-3: scheduled polling is a single dispatcher tick. The old
    `capacity-poll-system` / `capacity-poll-config` sweep beats are gone;
    per-device `poll_device_task` and the manual `poll_all` are NOT scheduled.
    """
    from app.config import get_settings
    from app.workers.celery_app import celery

    settings = get_settings()
    schedule = celery.conf.beat_schedule

    assert "capacity-dispatch-due" in schedule, list(schedule)
    entry = schedule["capacity-dispatch-due"]
    assert entry["task"] == "capacity.dispatch_due"
    assert entry["schedule"] == float(settings.POLL_DISPATCH_TICK_SECONDS)

    # Old sweep beats removed.
    assert "capacity-poll-system" not in schedule
    assert "capacity-poll-config" not in schedule
    # Manual / per-device tasks are not scheduled.
    scheduled_tasks = {e.get("task") for e in schedule.values()}
    assert "capacity.poll_all" not in scheduled_tasks
    assert "capacity.poll_device_task" not in scheduled_tasks


def test_polling_tasks_routed_to_polling_queue():
    """The dispatcher + per-device poll task ride the dedicated `polling`
    queue (consumed by the pan-ops-console-poller worker) so a long poll
    cycle can't block upgrade jobs on the default queue."""
    from app.workers.celery_app import celery

    routes = celery.conf.task_routes or {}
    assert routes.get("capacity.dispatch_due", {}).get("queue") == "polling"
    assert routes.get("capacity.poll_device_task", {}).get("queue") == "polling"


def test_panorama_sync_all_task_registered():
    """The scheduled Panorama sync wraps the existing sync_all service
    so Device.connected / last_seen_at stay fresh between operator
    interactions. The Celery task name 'panorama.sync_all' is what beat
    dispatches by — if the registered name drifts from the beat-schedule
    entry's `task:` field, beat will fire into the void and the field
    silently goes stale within minutes.
    """
    import app.core.panorama.tasks  # noqa: F401
    from app.workers.celery_app import celery

    assert "panorama.sync_all" in celery.tasks, (
        f"panorama.sync_all not registered. Available: "
        f"{[n for n in celery.tasks if not n.startswith('celery.')]}"
    )


def test_panorama_sync_beat_schedule_entry():
    """The beat schedule must reference the task by the same name the
    decorator registered, on the configurable PANORAMA_SYNC_INTERVAL_SECONDS
    cadence (default 300s).

    Operator-visible consequence if this regresses: the disconnected
    badge in the UI is no longer a real signal — sync only fires on
    manual click, so any device shows stale state minutes after.
    """
    from app.config import get_settings
    from app.workers.celery_app import celery

    settings = get_settings()
    schedule = celery.conf.beat_schedule
    assert "panorama-sync-all" in schedule, (
        f"panorama.sync_all not in beat_schedule. Entries: {list(schedule)}"
    )
    entry = schedule["panorama-sync-all"]
    assert entry["task"] == "panorama.sync_all"
    assert entry["schedule"] == float(settings.PANORAMA_SYNC_INTERVAL_SECONDS)
