"""CRUD for user-defined readiness check presets.

These are referenced from the Devices page Pre-check menu — operators
pick a preset, the device-level /precheck endpoint receives the explicit
checks list, and the precheck service runs exactly that subset.

We don't validate that check names are in ALL_READINESS_CHECKS on write —
the underlying service silently ignores unknown checks, and that's the
right behavior for forward-compat with library updates that add or
rename checks.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.db import get_db
from app.models.precheck_set import PrecheckSet
from app.models.user import User

router = APIRouter(prefix="/api/precheck-sets", tags=["precheck-sets"])


class PrecheckSetIn(BaseModel):
    name: str
    description: str | None = None
    checks: list[str]
    is_default: bool = False


class PrecheckSetOut(BaseModel):
    id: int
    name: str
    description: str | None
    checks: list[str]
    is_default: bool

    model_config = {"from_attributes": True}


@router.get("", response_model=list[PrecheckSetOut])
def list_sets(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    return (
        db.query(PrecheckSet)
        .order_by(PrecheckSet.is_default.desc(), PrecheckSet.name)
        .all()
    )


@router.post("", response_model=PrecheckSetOut, status_code=201)
def create_set(
    payload: PrecheckSetIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not payload.checks:
        raise HTTPException(400, "A pre-check set needs at least one check")
    if payload.is_default:
        # Single-default invariant: clear the flag on any existing default.
        db.query(PrecheckSet).filter(PrecheckSet.is_default.is_(True)).update(
            {PrecheckSet.is_default: False}
        )
    s = PrecheckSet(
        name=payload.name.strip(),
        description=payload.description,
        checks=list(payload.checks),
        is_default=payload.is_default,
        created_by_user_id=user.id,
    )
    db.add(s)
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(409, f"Could not create set: {exc}") from exc
    db.refresh(s)
    return s


@router.put("/{set_id}", response_model=PrecheckSetOut)
def update_set(
    set_id: int,
    payload: PrecheckSetIn,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    s = db.get(PrecheckSet, set_id)
    if not s:
        raise HTTPException(404, "Set not found")
    if not payload.checks:
        raise HTTPException(400, "A pre-check set needs at least one check")
    if payload.is_default and not s.is_default:
        db.query(PrecheckSet).filter(
            PrecheckSet.is_default.is_(True), PrecheckSet.id != s.id
        ).update({PrecheckSet.is_default: False})
    s.name = payload.name.strip()
    s.description = payload.description
    s.checks = list(payload.checks)
    s.is_default = payload.is_default
    db.commit()
    db.refresh(s)
    return s


@router.delete("/{set_id}", status_code=204)
def delete_set(
    set_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    s = db.get(PrecheckSet, set_id)
    if not s:
        raise HTTPException(404, "Set not found")
    db.delete(s)
    db.commit()
