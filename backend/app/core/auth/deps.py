"""FastAPI dependencies for resolving the current user from the session cookie.

Three flavors:

    current_user()        — required; 401 if not signed in
    current_user_optional — None if not signed in (for endpoints that vary
                            their behavior, e.g. bootstrap-status)
    current_admin()       — required + must be admin (403 otherwise)
"""

from __future__ import annotations

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session as DBSession

from app.config import get_settings
from app.core.auth.models.user import User
from app.core.auth.services.sessions import lookup_session
from app.db import get_db


def _get_cookie_name() -> str:
    return get_settings().SESSION_COOKIE_NAME


def current_user_optional(
    db: DBSession = Depends(get_db),
    # Default cookie name; the actual name comes from settings. We can't
    # reference settings at module load AND have FastAPI inspect the
    # parameter, so the cookie name string is hardcoded to match the
    # settings default. If you customize SESSION_COOKIE_NAME, update both.
    session: str | None = Cookie(default=None, alias="pcasession"),
) -> User | None:
    row = lookup_session(db, session)
    return row.user if row else None


def current_user(
    user: User | None = Depends(current_user_optional),
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
        )
    return user


def current_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin required",
        )
    return user
