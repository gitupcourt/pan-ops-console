"""Authentication routes.

Surface:
    GET  /auth/bootstrap-status    — public; tells the UI which login flow to show
    POST /auth/signup-first        — public; ONLY works when no users exist
    POST /auth/login               — public; sets the session cookie. May return
                                     { needs_totp: true } at status 200 when a
                                     password is valid but TOTP is required.
    POST /auth/logout              — sets cookie to expired; revokes server-side row
    GET  /auth/me                  — required; returns the current user
    POST /auth/change-password     — required; rotates own password, revokes other sessions
    POST /auth/totp/setup          — required; generates a fresh TOTP secret
    POST /auth/totp/verify         — required; enrolls TOTP, returns backup codes
    POST /auth/totp/disable        — required; clears TOTP (gated by password)

OIDC endpoints land in slice 3.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import Response as RawResponse
from sqlalchemy.orm import Session as DBSession

from app.config import get_settings
from app.db import get_db
from app.models.user import BackupCode, User
from app.schemas import (
    BootstrapStatus,
    LoginNeedsTOTPResponse,
    LoginRequest,
    PasswordChangeRequest,
    SignupFirstRequest,
    TOTPDisableRequest,
    TOTPSetupResponse,
    TOTPVerifyRequest,
    TOTPVerifyResponse,
    UserRead,
)
from app.services import totp
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


def _set_session_cookie(response, token: str) -> None:
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


def _clear_session_cookie(response) -> None:
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


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DBSession = Depends(get_db),
):
    """Sign in flow.

    Three outcomes:
      - password wrong / unknown user / inactive → 401
      - password right, TOTP required, code missing or wrong → 200 with
        {"needs_totp": true}. The frontend renders the TOTP input and
        re-submits with `totp_code` populated.
      - password right, no TOTP (or TOTP correct) → 200 with the user
        object and a session cookie set.
    """
    user = db.query(User).filter(User.username == payload.username).first()
    pwd_ok = verify_password(payload.password, user.password_hash if user else None)
    if user is None or not pwd_ok or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )

    if user.totp_enabled:
        if not payload.totp_code:
            return LoginNeedsTOTPResponse()
        if not _consume_totp_or_backup(db, user, payload.totp_code):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid credentials",
            )

    if user.password_hash and needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
        db.commit()

    token = create_session(db, user, user_agent=request.headers.get("user-agent"))
    _set_session_cookie(response, token)
    return UserRead.model_validate(user)


def _consume_totp_or_backup(db: DBSession, user: User, code: str) -> bool:
    """Try TOTP first, then backup codes. Backup codes are single-use;
    the matching row is deleted on success."""
    if user.encrypted_totp_secret:
        try:
            secret = totp.decrypt_secret(user.encrypted_totp_secret)
            if totp.verify_code(secret, code):
                return True
        except Exception:
            pass

    h = totp.hash_backup_code(code)
    row = db.query(BackupCode).filter(
        BackupCode.user_id == user.id, BackupCode.code_hash == h
    ).first()
    if row is not None:
        db.delete(row)
        db.commit()
        return True
    return False


@router.post("/logout")
def logout(
    request: Request,
    db: DBSession = Depends(get_db),
):
    token = request.cookies.get(get_settings().SESSION_COOKIE_NAME)
    if token:
        revoke_session(db, token)
    resp = RawResponse(status_code=204)
    _clear_session_cookie(resp)
    return resp


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(current_user)) -> UserRead:
    return user


@router.post("/change-password")
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    db: DBSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Rotate the current user's password. Revokes every OTHER session
    (cookie on this device stays valid via the freshly minted token)."""
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

    revoke_all_for_user(db, user.id)
    token = create_session(db, user, user_agent=request.headers.get("user-agent"))
    resp = RawResponse(status_code=204)
    _set_session_cookie(resp, token)
    return resp


# =====================================================================
# TOTP enrollment
#
# Two-step:
#   POST /auth/totp/setup   -> returns {secret, otpauth_uri}. Stores the
#                              encrypted secret on the user, but does NOT
#                              set totp_enabled. User scans QR / pastes
#                              secret into their authenticator app.
#   POST /auth/totp/verify  -> user submits a 6-digit code. If valid,
#                              totp_enabled flips to true AND 10 backup
#                              codes are generated, hashed-and-stored,
#                              and returned plaintext ONCE.
#
# Restarting setup at any time wipes any in-progress secret + codes, so
# a half-finished enrollment never leaves a stale secret around.
# =====================================================================

@router.post("/totp/setup", response_model=TOTPSetupResponse)
def totp_setup(
    db: DBSession = Depends(get_db),
    user: User = Depends(current_user),
):
    if user.totp_enabled:
        raise HTTPException(
            status_code=400,
            detail="TOTP is already enabled; disable it first to re-enroll",
        )
    secret = totp.generate_secret()
    user.encrypted_totp_secret = totp.encrypt_secret(secret)
    db.query(BackupCode).filter(BackupCode.user_id == user.id).delete()
    db.commit()
    return TOTPSetupResponse(
        secret=secret,
        otpauth_uri=totp.provisioning_uri(secret, user.username),
    )


@router.post("/totp/verify", response_model=TOTPVerifyResponse)
def totp_verify(
    payload: TOTPVerifyRequest,
    db: DBSession = Depends(get_db),
    user: User = Depends(current_user),
):
    if user.totp_enabled:
        raise HTTPException(status_code=400, detail="TOTP is already enabled")
    if not user.encrypted_totp_secret:
        raise HTTPException(
            status_code=400,
            detail="no in-progress enrollment — call /auth/totp/setup first",
        )
    secret = totp.decrypt_secret(user.encrypted_totp_secret)
    if not totp.verify_code(secret, payload.code):
        raise HTTPException(status_code=400, detail="incorrect code")

    user.totp_enabled = True
    codes = totp.generate_backup_codes()
    for code in codes:
        db.add(BackupCode(user_id=user.id, code_hash=totp.hash_backup_code(code)))
    db.commit()
    return TOTPVerifyResponse(backup_codes=codes)


@router.post("/totp/disable")
def totp_disable(
    payload: TOTPDisableRequest,
    db: DBSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Disable TOTP. Requires the user's password as a second factor
    against session hijack — if someone walks up to an unlocked laptop,
    they shouldn't be able to silently strip 2FA."""
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=400, detail="incorrect password")
    user.totp_enabled = False
    user.encrypted_totp_secret = None
    db.query(BackupCode).filter(BackupCode.user_id == user.id).delete()
    db.commit()
    return RawResponse(status_code=204)
