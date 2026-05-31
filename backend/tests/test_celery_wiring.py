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

    Beat looks tasks up by string name; if a task is unregistered or
    renamed, beat silently does nothing. #89 split scheduled polling into
    `capacity.poll_config_metrics` (slow) + `capacity.poll_system_metrics`
    (fast); `capacity.poll_all` stays registered for the manual
    "poll now" route.
    """
    # Importing the tasks module is what registers the @celery.task
    # decorator. The worker `include` list does this implicitly at boot;
    # the test does it explicitly so registration is observable.
    import app.capacity.tasks  # noqa: F401
    from app.workers.celery_app import celery

    available = [name for name in celery.tasks if not name.startswith("celery.")]
    for name in (
        "capacity.poll_all",
        "capacity.poll_config_metrics",
        "capacity.poll_system_metrics",
    ):
        assert name in celery.tasks, f"{name} not registered. Available: {available}"


def test_capacity_beat_schedule_split_cadences():
    """#89 split capacity polling into two beats: a fast one for live
    telemetry and a slow one for config-class metrics.

    Guards both entries point at the right task and read the right
    interval setting. `capacity.poll_all` must NOT be on the schedule —
    scheduling it alongside the system beat would double-poll the fast
    metrics every fast cycle.
    """
    from app.config import get_settings
    from app.workers.celery_app import celery

    settings = get_settings()
    schedule = celery.conf.beat_schedule

    assert "capacity-poll-system" in schedule, list(schedule)
    sys_entry = schedule["capacity-poll-system"]
    assert sys_entry["task"] == "capacity.poll_system_metrics"
    assert sys_entry["schedule"] == float(settings.POLL_SYSTEM_INTERVAL_SECONDS)

    assert "capacity-poll-config" in schedule, list(schedule)
    cfg_entry = schedule["capacity-poll-config"]
    assert cfg_entry["task"] == "capacity.poll_config_metrics"
    assert cfg_entry["schedule"] == float(settings.POLL_CONFIG_INTERVAL_SECONDS)

    # The full-sweep task is intentionally manual-only.
    assert not any(
        e.get("task") == "capacity.poll_all" for e in schedule.values()
    ), "capacity.poll_all must not be scheduled (would double-poll fast metrics)"


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
