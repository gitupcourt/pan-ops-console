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
from sqlalchemy.orm import configure_mappers

from app.config import get_settings

# Eager-import every module's models package so the SQLAlchemy class
# registry is fully populated *before* the worker tries to configure
# any mapper.
#
# This matters because Device has `relationship("Panorama", ...)` —
# resolved by string at mapper-configure time. The worker entry point
# (`celery -A app.workers.celery_app:celery worker`) doesn't go
# through `app.main`'s import chain, so without these explicit imports
# the worker process gets Device loaded (via app.capacity.tasks →
# poller → device model) but NOT Panorama, and the first task
# execution dies with:
#
#   sqlalchemy.exc.InvalidRequestError: When initializing mapper
#   Mapper[Device(devices)], expression 'Panorama' failed to locate
#   a name ('Panorama').
#
# This pattern (eager registry population) mirrors `app.main`'s
# matching block. Adding a new module's models package: list it both
# here and in app.main.
#
# Fix for pan-ops-console#39 (caught at phase-2e cutover).
from app.capacity import models as _capacity_models  # noqa: E402, F401
from app.core.auth import models as _auth_models  # noqa: E402, F401
from app.core.devices import models as _devices_models  # noqa: E402, F401
from app.core.panorama import models as _panorama_models  # noqa: E402, F401
from app.upgrade import models as _upgrade_models  # noqa: E402, F401

# Force registry resolution NOW (at worker boot) instead of at first
# task execution. A missing class — same shape as the bug above —
# fails the worker pod's liveness probe loudly rather than silently
# accepting tasks and dying mid-execute.
configure_mappers()

_settings = get_settings()

celery = Celery(
    "panops",
    broker=_settings.REDIS_URL,
    backend=_settings.REDIS_URL,
    include=[
        # Modules that define @celery.task functions. Adding a new task
        # module means listing it here so the worker imports it at boot.
        "app.capacity.tasks",
        "app.core.panorama.tasks",
        "app.upgrade.tasks",
    ],
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Retain the pre-Celery-6.0 behavior (retry the broker connection on
    # worker startup) and silence the CPendingDeprecationWarning that prints
    # on every worker/beat boot otherwise.
    broker_connection_retry_on_startup=True,
    # `upgrade.drive_pair` runs with acks_late + reject_on_worker_lost (see
    # app.upgrade.tasks) so a task killed by a worker restart/loss is REDELIVERED
    # and resumed rather than orphaned. For that to be safe the Redis broker's
    # visibility timeout MUST exceed the task's hard time limit (155 min) —
    # otherwise Redis would redeliver a still-running drive_pair to a second
    # worker process and double-drive the same HA pair. 3 h gives clear margin
    # over the 155-min ceiling. (Early-acked tasks like capacity polling ack on
    # receipt, so this window never applies to them.)
    broker_transport_options={"visibility_timeout": 3 * 60 * 60},
    # task_track_started=True is what makes task.state == 'STARTED'
    # observable from the API side. The UI uses it to distinguish
    # "queued" from "actively running" — load-bearing for the
    # upgrade-job progress UI in phase 4. Don't turn it off.
    task_track_started=True,
    # Long-running upgrade tasks (install + reboot can take 30+ minutes)
    # need results to stick around long enough for the UI to read them
    # post-completion. 1 day is comfortable.
    result_expires=86400,
    # Queue routing (#89 PR-3; platform deploy contract pan-ops-console#137).
    # Upgrade orchestration (`upgrade.drive_pair`) is the long-running,
    # memory-hungry work (snapshot + XML diff), so it gets its OWN queue +
    # dedicated, larger worker — that way a 12-min capacity poll can never
    # delay an upgrade, and the upgrade worker can be sized with more RAM.
    # Everything else (capacity dispatch + per-device polls, panorama sync,
    # manual poll_all) stays on the DEFAULT `celery` queue, kept as the
    # catch-all so no task can ever strand without a consumer. The existing
    # worker retunes to capacity-only (`-Q celery`); platform owns the new
    # upgrade worker (`-Q upgrade`) and lands it lockstep with this image.
    task_routes={
        "upgrade.*": {"queue": "upgrade"},
    },
    # Beat schedule. Capacity polling (#89 PR-3) is driven by a single
    # lightweight dispatcher tick — `capacity.dispatch_due` finds devices
    # whose per-class interval has elapsed and enqueues bounded, per-Panorama
    # batches of `capacity.poll_device_task`. This replaced the old
    # "sweep everything every interval" beats (which overlapped and hammered
    # one Panorama). `capacity.poll_all` stays defined for the manual
    # "poll now" route but is deliberately NOT scheduled.
    beat_schedule={
        "capacity-dispatch-due": {
            "task": "capacity.dispatch_due",
            "schedule": float(_settings.POLL_DISPATCH_TICK_SECONDS),
        },
        # Scheduled refresh of Panorama-imported device state. Without
        # this, Device.connected / last_seen_at only update on operator
        # action (manual sync, device import) and the UI's disconnected
        # badge becomes meaningless within minutes. The sync_all task is
        # idempotent and one-API-call-per-Panorama.
        "panorama-sync-all": {
            "task": "panorama.sync_all",
            "schedule": float(_settings.PANORAMA_SYNC_INTERVAL_SECONDS),
        },
    },
)
