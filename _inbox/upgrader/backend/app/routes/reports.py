"""Upgrade reports (stub)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.db import get_db
from app.models.user import User

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/jobs/{job_id}")
def get_job_report(
    job_id: int,
    _db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """TODO: render an HTML or PDF report from job + tasks + snapshot diffs."""
    raise HTTPException(501, "Report generation not yet implemented")


@router.post("/jobs/{job_id}/email")
def email_job_report(
    job_id: int,
    _db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """TODO: render report and send via SMTP using settings.SMTP_*."""
    raise HTTPException(501, "Report emailing not yet implemented")
