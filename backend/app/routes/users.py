"""Admin-only user management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session as DBSession

from app.db import get_db
from app.models.user import User
from app.schemas import UserCreate, UserRead
from app.services.auth_dep import current_admin, current_user
from app.services.passwords import (
    PasswordPolicyError,
    check_password_strength,
    hash_password,
)
from app.services.sessions import revoke_all_for_user

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserRead])
def list_users(
    db: DBSession = Depends(get_db),
    _: User = Depends(current_admin),
):
    return db.query(User).order_by(User.username).all()


@router.post("", response_model=UserRead, status_code=201)
def create_user(
    payload: UserCreate,
    db: DBSession = Depends(get_db),
    _: User = Depends(current_admin),
):
    if db.query(User).filter(User.username == payload.username).first() is not None:
        raise HTTPException(status_code=409, detail="username already taken")
    try:
        check_password_strength(
            payload.password,
            user_inputs=[payload.username, payload.email or ""],
        )
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        is_admin=payload.is_admin,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}/active", response_model=UserRead)
def set_active(
    user_id: int,
    active: bool,
    db: DBSession = Depends(get_db),
    actor: User = Depends(current_admin),
):
    """Activate or deactivate a user. Deactivating revokes all their sessions."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="not found")
    if target.id == actor.id and not active:
        raise HTTPException(status_code=400, detail="you cannot deactivate yourself")
    target.is_active = active
    db.commit()
    if not active:
        revoke_all_for_user(db, target.id)
    db.refresh(target)
    return target


@router.patch("/{user_id}/admin", response_model=UserRead)
def set_admin(
    user_id: int,
    is_admin: bool,
    db: DBSession = Depends(get_db),
    actor: User = Depends(current_admin),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="not found")
    if target.id == actor.id and not is_admin:
        raise HTTPException(
            status_code=400,
            detail="you cannot demote yourself — ask another admin",
        )
    target.is_admin = is_admin
    db.commit()
    db.refresh(target)
    return target


@router.delete("/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    db: DBSession = Depends(get_db),
    actor: User = Depends(current_admin),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="not found")
    if target.id == actor.id:
        raise HTTPException(status_code=400, detail="you cannot delete yourself")
    db.delete(target)
    db.commit()
