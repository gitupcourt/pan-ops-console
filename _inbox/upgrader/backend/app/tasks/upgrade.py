"""Celery tasks for upgrade orchestration.

A Job dispatches one `drive_pair_task` per HA pair (or per standalone device).
Each driver is long-running — it walks all phases imperatively, persists
progress to DeviceUpgradeTask rows, and parks at confirmation gates by
polling the DB. The HTTP layer offers /confirm and /abort to influence those.
"""

from __future__ import annotations

import logging

from app.celery_app import celery
from app.db import SessionLocal
from app.models.enums import JobState
from app.models.job import DeviceUpgradeTask, UpgradeJob
from app.services import upgrade as upgrade_svc

log = logging.getLogger(__name__)


@celery.task(name="upgrade.run_job")
def run_job(job_id: int) -> dict:
    """Fan out one driver task per HA pair / standalone in this job."""
    db = SessionLocal()
    try:
        job = db.get(UpgradeJob, job_id)
        if job is None:
            return {"job_id": job_id, "ok": False, "error": "job not found"}
        if job.state in (JobState.COMPLETED, JobState.FAILED, JobState.ABORTED):
            return {"job_id": job_id, "ok": False, "error": f"job is {job.state}"}

        # Mark running.
        from datetime import datetime, timezone
        job.state = JobState.RUNNING
        if job.started_at is None:
            job.started_at = datetime.now(timezone.utc)
        db.commit()

        # Group tasks by ha_pair_key.
        keys = sorted({
            t.ha_pair_key
            for t in db.query(DeviceUpgradeTask).filter(DeviceUpgradeTask.job_id == job_id).all()
        })
        for key in keys:
            drive_pair_task.delay(job_id, key)

        return {"job_id": job_id, "ok": True, "pairs": len(keys)}
    finally:
        db.close()


@celery.task(name="upgrade.drive_pair", bind=True, max_retries=0)
def drive_pair_task(self, job_id: int, ha_pair_key: str) -> dict:
    """Drive one HA pair (or one standalone) through all phases."""
    upgrade_svc.drive_pair(job_id, ha_pair_key)
    return {"job_id": job_id, "ha_pair_key": ha_pair_key, "ok": True}


# Legacy stub name preserved in case other code imports it. The new flow
# doesn't use per-tick advancement.
@celery.task(name="upgrade.advance_task", bind=True, max_retries=0)
def advance_task(self, task_id: int) -> dict:  # noqa: ARG001
    return {"task_id": task_id, "ok": True, "note": "deprecated; drive_pair runs the whole flow"}
