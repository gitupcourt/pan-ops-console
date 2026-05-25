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


def test_beat_schedule_is_empty_until_phase_2e():
    """Sanity check on the no-behavior-change discipline of phase 2c.

    If a beat_schedule entry sneaks in here, capacity polling would
    double-fire (APScheduler + beat) the moment a beat process is
    started. That's a phase 2e change, not 2c.
    """
    from app.workers.celery_app import celery

    assert celery.conf.beat_schedule == {}, (
        "beat_schedule should be empty until phase 2e wires the cutover"
    )
