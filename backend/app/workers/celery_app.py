"""Celery application factory.

This Celery app is **defined** but not yet **driven** in dev/prod:
- No worker Deployment exists in the cluster until phase 2d.
- No beat schedule is configured until phase 2e (when capacity polling
  flips from in-process APScheduler to a Celery beat task).
- Tests don't import this module — they stay on the synchronous
  scheduler path.

Defining the app now (phase 2c) means task modules can be authored under
`app/<module>/tasks/` and imported by name (via Celery's `include`)
without further plumbing when the cluster side comes up. The capacity
poller already has a Celery-callable wrapper at
`app.capacity.tasks.poll_all` — dormant, but ready to wire to beat.

Mirrors the upgrader's celery_app.py shape (per MIGRATION_NOTES §6):
JSON serializer, UTC timezone, task_track_started for "queued vs
running" UI signal, 1-day result_expires so long-running tasks' results
don't vanish before the UI can read them.
"""

from __future__ import annotations

from celery import Celery

from app.config import get_settings

_settings = get_settings()

celery = Celery(
    "panops",
    broker=_settings.REDIS_URL,
    backend=_settings.REDIS_URL,
    include=[
        # Modules that define @celery.task functions. Adding a new task
        # module means listing it here so the worker imports it at boot.
        "app.capacity.tasks",
        # Phase 4d will add: "app.upgrade.tasks"
    ],
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # task_track_started=True is what makes task.state == 'STARTED'
    # observable from the API side. The UI uses it to distinguish
    # "queued" from "actively running" — load-bearing for the
    # upgrade-job progress UI in phase 4. Don't turn it off.
    task_track_started=True,
    # Long-running upgrade tasks (install + reboot can take 30+ minutes)
    # need results to stick around long enough for the UI to read them
    # post-completion. 1 day is comfortable.
    result_expires=86400,
    # Beat schedule deliberately empty in phase 2c. Phase 2e adds:
    #   "capacity-poll-all": {
    #       "task": "capacity.poll_all",
    #       "schedule": settings.POLL_INTERVAL_SECONDS,
    #   }
    beat_schedule={},
)
