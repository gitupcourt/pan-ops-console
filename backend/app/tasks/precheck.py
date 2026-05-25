"""Celery tasks for pre-checks (bulk fan-out)."""

import logging

from app.celery_app import celery
from app.db import SessionLocal
from app.models.device import Device
from app.services import precheck as precheck_svc

log = logging.getLogger(__name__)


@celery.task(name="precheck.run_device_precheck", bind=True, max_retries=0)
def run_device_precheck_task(
    self,
    device_id: int,
    bulk_run_id: int | None = None,
    checks: list[str] | None = None,
    user_id: int | None = None,
) -> dict:
    """Run a precheck against a single device, persist as part of `bulk_run_id`.

    Returns a small status dict for visibility in the Celery result backend; the
    real persisted record is the PrecheckRun row, which the UI polls via
    GET /api/devices/precheck/bulk/{id}.
    """
    db = SessionLocal()
    try:
        device = db.get(Device, device_id)
        if not device:
            log.warning("run_device_precheck: device %s not found", device_id)
            return {"device_id": device_id, "ok": False, "error": "device not found"}

        run = precheck_svc.run_precheck_for_device(
            db,
            device,
            checks=checks,
            user_id=user_id,
            bulk_run_id=bulk_run_id,
        )
        return {
            "device_id": device_id,
            "run_id": run.id,
            "overall": run.overall_severity.value,
            "ok": run.error is None,
        }
    finally:
        db.close()
