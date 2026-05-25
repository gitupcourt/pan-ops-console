"""Scheduled refresh tasks (Celery beat -> worker)."""

import logging

from app.celery_app import celery
from app.db import SessionLocal
from app.services.panorama_sync import sync_all

log = logging.getLogger(__name__)


@celery.task(name="refresh.sync_all_panoramas")
def sync_all_panoramas() -> dict[int, int]:
    """Refresh devices from every configured Panorama. Run periodically by beat."""
    db = SessionLocal()
    try:
        results = sync_all(db)
        log.info("Scheduled refresh complete: %s", results)
        return results
    finally:
        db.close()
