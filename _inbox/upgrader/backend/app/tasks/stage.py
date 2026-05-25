"""Celery tasks for pre-staging PAN-OS images."""

import logging

from app.celery_app import celery
from app.db import SessionLocal
from app.models.device import Device
from app.services import stage as stage_svc

log = logging.getLogger(__name__)


@celery.task(name="stage.run_device_stage", bind=True, max_retries=0)
def run_device_stage_task(
    self,
    device_id: int,
    version: str,
    bulk_run_id: int | None = None,
) -> dict:
    """Stage `version` on a single device. Long-running (download + poll)."""
    db = SessionLocal()
    try:
        device = db.get(Device, device_id)
        if not device:
            return {"device_id": device_id, "ok": False, "error": "device not found"}

        run = stage_svc.stage_device_for_version(
            db, device, version, bulk_run_id=bulk_run_id
        )
        return {
            "device_id": device_id,
            "run_id": run.id,
            "outcome": run.outcome.value,
            "ok": run.outcome.value == "pass",
        }
    finally:
        db.close()
