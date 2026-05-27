"""Precheck-set CRUD + the available-checks lookup.

A `PrecheckSet` is an operator-curated ordered list of readiness-
check names that gets run as the pre/post phase of an upgrade job.
Phase 13b adds the CRUD API + JobForm dropdown integration; the
table itself shipped in phase 4c-models migration 0003 and was
seeded with two presets ("Standard", "Full") in migration 0007.

Uniqueness contract: `name` is unique. `is_default` is logically
unique-true (at most one row flagged) but enforced here, not at the
schema level — newer DB engines have partial-unique-index options
but SQLite doesn't, and the per-op enforcement is simple enough.

Routes:
  - GET    /upgrade/precheck-sets/             list all
  - GET    /upgrade/precheck-sets/available    list catalog check names
  - POST   /upgrade/precheck-sets/             create
  - PATCH  /upgrade/precheck-sets/{id}         partial update
  - DELETE /upgrade/precheck-sets/{id}         hard delete
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.command_proxy.pan_client import (
    ALL_READINESS_CHECKS,
    DEFAULT_READINESS_CHECKS,
)
from app.db import get_db
from app.upgrade.models.precheck_set import PrecheckSet

router = APIRouter(prefix="/upgrade/precheck-sets", tags=["upgrade"])


# ---------- Schemas ----------


class PrecheckSetRead(BaseModel):
    id: int
    name: str
    description: str | None
    checks: list[str]
    is_default: bool
    created_at: datetime
    updated_at: datetime


class PrecheckSetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    checks: list[str] = Field(min_length=1)
    is_default: bool = False


class PrecheckSetUpdate(BaseModel):
    """Partial — only fields the operator sent.

    Note: `name` IS editable here (unlike alert rules), because the
    name has no functional consequence — it's just a label. The unique
    constraint catches collisions.
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    checks: list[str] | None = Field(default=None, min_length=1)
    is_default: bool | None = None


class AvailableChecks(BaseModel):
    """Catalog of every readiness-check name the backend knows about,
    plus the hard-coded default list so the UI can offer a "reset to
    defaults" affordance.
    """

    all: list[str]
    default: list[str]


# ---------- Routes ----------


@router.get("", response_model=list[PrecheckSetRead])
def list_precheck_sets(db: Session = Depends(get_db)) -> list[PrecheckSetRead]:
    rows = db.execute(
        select(PrecheckSet).order_by(PrecheckSet.is_default.desc(), PrecheckSet.name.asc())
    ).scalars().all()
    return [_to_read(r) for r in rows]


@router.get("/available", response_model=AvailableChecks)
def list_available_checks() -> AvailableChecks:
    """The catalog of check names operators can put into a set.

    Sourced from `app.core.command_proxy.pan_client` constants so a
    new check added to the library shows up here on the next
    backend restart without a separate migration. The UI uses
    `all` to render a multi-select check picker and `default` to
    offer a "fill with defaults" button on the create form.
    """
    return AvailableChecks(
        all=sorted(ALL_READINESS_CHECKS),
        default=list(DEFAULT_READINESS_CHECKS),
    )


@router.post("", response_model=PrecheckSetRead, status_code=201)
def create_precheck_set(
    body: PrecheckSetCreate,
    db: Session = Depends(get_db),
) -> PrecheckSetRead:
    """Add a new set. 409 on name conflict.

    If `is_default=True`, any other row's `is_default` is flipped to
    false in the same transaction so the unique-default invariant
    holds.
    """
    if body.is_default:
        _clear_default_flag(db)
    row = PrecheckSet(
        name=body.name,
        description=body.description,
        checks=body.checks,
        is_default=body.is_default,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"A precheck set named {body.name!r} already exists.",
        ) from exc
    db.refresh(row)
    return _to_read(row)


@router.patch("/{set_id}", response_model=PrecheckSetRead)
def update_precheck_set(
    set_id: int,
    body: PrecheckSetUpdate,
    db: Session = Depends(get_db),
) -> PrecheckSetRead:
    row = db.get(PrecheckSet, set_id)
    if row is None:
        raise HTTPException(status_code=404, detail="precheck set not found")
    # Flip default last so a self-flip doesn't clear itself.
    if body.is_default is True:
        _clear_default_flag(db, except_id=set_id)
    if body.name is not None:
        row.name = body.name
    if body.description is not None:
        row.description = body.description
    if body.checks is not None:
        row.checks = body.checks
    if body.is_default is not None:
        row.is_default = body.is_default
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Name conflict with another precheck set.",
        ) from exc
    db.refresh(row)
    return _to_read(row)


@router.delete("/{set_id}", status_code=204)
def delete_precheck_set(set_id: int, db: Session = Depends(get_db)):
    """Hard delete. Jobs that referenced this set are not blocked —
    the FK is ON DELETE SET NULL so the job falls back to the
    next-available default."""
    row = db.get(PrecheckSet, set_id)
    if row is None:
        raise HTTPException(status_code=404, detail="precheck set not found")
    db.delete(row)
    db.commit()


# ---------- Helpers ----------


def _clear_default_flag(db: Session, *, except_id: int | None = None) -> None:
    """Set is_default=false on every row except optionally one. Used
    before flipping a different row to default."""
    stmt = update(PrecheckSet).values(is_default=False)
    if except_id is not None:
        stmt = stmt.where(PrecheckSet.id != except_id)
    db.execute(stmt)


def _to_read(r: PrecheckSet) -> PrecheckSetRead:
    return PrecheckSetRead(
        id=r.id,
        name=r.name,
        description=r.description,
        checks=list(r.checks),
        is_default=r.is_default,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )
