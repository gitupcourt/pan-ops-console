"""Celery application instance.

Workers are launched with:
    celery -A app.celery_app.celery worker --loglevel=info

Scheduled jobs (Celery beat):
    celery -A app.celery_app.celery beat --loglevel=info
"""

from celery import Celery
from celery.schedules import crontab  # noqa: F401  (handy for future cron schedules)

from app.config import get_settings

settings = get_settings()

celery = Celery(
    "panfw",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.upgrade", "app.tasks.refresh", "app.tasks.precheck", "app.tasks.stage"],
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # Long-running upgrades can stretch over an hour; keep results around for a day.
    result_expires=86400,
    beat_schedule={
        "refresh-panoramas": {
            "task": "refresh.sync_all_panoramas",
            "schedule": settings.PANORAMA_REFRESH_INTERVAL_MINUTES * 60.0,
        },
    },
)
