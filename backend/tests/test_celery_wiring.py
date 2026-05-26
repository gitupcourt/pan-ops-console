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


def test_capacity_poll_all_task_registered():
    """The capacity poller's Celery task must be discoverable by name.

    Phase 2e's beat schedule entry looks up the task by the string
    "capacity.poll_all"; if the task is unregistered or named
    differently, beat silently does nothing.
    """
    # Importing the tasks module is what registers the @celery.task
    # decorator. The worker `include` list does this implicitly at boot;
    # the test does it explicitly so registration is observable.
    import app.capacity.tasks  # noqa: F401
    from app.workers.celery_app import celery

    assert "capacity.poll_all" in celery.tasks, (
        f"capacity.poll_all not registered. Available: "
        f"{[name for name in celery.tasks if not name.startswith('celery.')]}"
    )


def test_capacity_beat_schedule_entry():
    """Phase 2e cutover wired capacity.poll_all into the beat schedule.

    The matching APScheduler removal lives in the same PR — the two
    must move in lockstep or polling would double-fire (during the
    transition window) or stop entirely. This test guards the beat
    side; the lifespan-no-longer-starts-APScheduler guard is the
    absence of a scheduler.start() call in app.main.
    """
    from app.config import get_settings
    from app.workers.celery_app import celery

    settings = get_settings()
    schedule = celery.conf.beat_schedule
    assert "capacity-poll-all" in schedule, (
        f"capacity.poll_all not in beat_schedule. Entries: {list(schedule)}"
    )
    entry = schedule["capacity-poll-all"]
    assert entry["task"] == "capacity.poll_all"
    assert entry["schedule"] == float(settings.POLL_INTERVAL_SECONDS)


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
