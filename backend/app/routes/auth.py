"""Authentication routes.

Surface:
    GET  /auth/bootstrap-status    — public; tells the UI which login flow to show
    POST /auth/signup-first        — public; ONLY works when no users exist
    POST /auth/login               — public; sets the session cookie
    POST /auth/logout              — sets cookie to expired; revokes server-side row
    GET  /auth/me                  — required; returns the current user
    POST /auth/change-password     — required; rotates own password, revokes other sessions

OIDC endpoints land in Slice 2 (Phase 2 in the plan).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session as DBSession

from app.config import get_settings
from app.db import get_db
from app.models.user import User
from app.schemas import (
    BootstrapStatus,
    LoginRequest,
    PasswordChangeRequest,
    SignupFirstRequest,
    UserRead,
)
from app.services.auth_dep import current_user
from app.services.passwords import (
    PasswordPolicyError,
    check_password_strength,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.services.sessions import create_session, revoke_all_for_user, revoke_session

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        key=s.SESSION_COOKIE_NAME,
        value=token,
        max_age=s.SESSION_LIFETIME_SECONDS,
        httponly=True,
        secure=s.SESSION_COOKIE_SECURE,
        samesite=s.SESSION_COOKIE_SAMESITE,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(
        key=s.SESSION_COOKIE_NAME,
        path="/",
        secure=s.SESSION_COOKIE_SECURE,
        samesite=s.SESSION_COOKIE_SAMESITE,
        httponly=True,
    )


@router.get("/bootstrap-status", response_model=BootstrapStatus)
def bootstrap_status(db: DBSession = Depends(get_db)) -> BootstrapStatus:
    """Probed by the SPA on first paint. If no users exist, the SPA renders
    the first-user setup screen instead of the login form."""
    has_any_user = db.query(User.id).first() is not None
    return BootstrapStatus(
        needs_bootstrap=not has_any_user,
        # Phase 2 will populate this from configured OIDC providers.
        oidc_providers=[],
    )


@router.post("/signup-first", response_model=UserRead, status_code=201)
def signup_first(
    payload: SignupFirstRequest,
    request: Request,
    response: Response,
    db: DBSession = Depends(get_db),
) -> UserRead:
    """Create the first user. Only works when no users exist; otherwise 403.

    The created user is auto-admin and logged in (session cookie set on the
    response). All subsequent user creation goes through admin /users routes.
    """
    if db.query(User.id).first() is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="bootstrap is already complete — sign in instead",
        )

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
        is_admin=True,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_session(db, user, user_agent=request.headers.get("user-agent"))
    _set_session_cookie(response, token)
    return user


@router.post("/login", response_model=UserRead)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DBSession = Depends(get_db),
) -> UserRead:
    user = db.query(User).filter(User.username == payload.username).first()
    # Always run verify to keep timing roughly constant across "user exists"
    # vs "doesn't" — small leak protection.
    pwd_ok = verify_password(payload.password, user.password_hash if user else None)
    if user is None or not pwd_ok or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )

    if user.totp_enabled:
        # Phase 1.5 — verify payload.totp_code. For now, surface a clear
        # error if a TOTP-enabled user tries to log in: the UI doesn't
        # know how to collect the code yet.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TOTP login not yet supported in this build",
        )

    # Re-hash with current Argon2 params if needed (transparent upgrade).
    if user.password_hash and needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
        db.commit()

    token = create_session(db, user, user_agent=request.headers.get("user-agent"))
    _set_session_cookie(response, token)
    return user


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    db: DBSession = Depends(get_db),
) -> None:
    token = request.cookies.get(get_settings().SESSION_COOKIE_NAME)
    if token:
        revoke_session(db, token)
    _clear_session_cookie(response)


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(current_user)) -> UserRead:
    return user


@router.post("/change-password", status_code=204)
def change_password(
    payload: PasswordChangeRequest,
    response: Response,
    request: Request,
    db: DBSession = Depends(get_db),
    user: User = Depends(current_user),
) -> None:
    """Rotate the current user's password. Revokes every OTHER session
    (cookie on this device stays valid)."""
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="current password is wrong")
    try:
        check_password_strength(
            payload.new_password,
            user_inputs=[user.username, user.email or ""],
        )
    except PasswordPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    user.password_hash = hash_password(payload.new_password)
    db.commit()

    # Revoke all sessions, then issue a fresh one for the current browser.
    revoke_all_for_user(db, user.id)
    token = create_session(db, user, user_agent=request.headers.get("user-agent"))
    _set_session_cookie(response, token)
