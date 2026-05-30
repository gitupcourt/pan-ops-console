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
from app.core.auth.deps import current_user
from app.core.auth.models.user import BackupCode, User
from app.core.auth.schemas import (
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
from app.core.auth.services import oidc, totp
from app.core.auth.services.passwords import (
    PasswordPolicyError,
    check_password_strength,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.core.auth.services.sessions import create_session, revoke_all_for_user, revoke_session
from app.db import get_db

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
        oidc_providers=oidc.list_provider_names(db),
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


# =====================================================================
# OIDC / Single Sign-On
#
# Providers are configured by an admin via /providers/oidc — the normal
# path. An env-var fallback (OIDC_PROVIDER_<NAME>_*) is also honored for
# pre-deploy bootstrap or air-gapped installs that need OIDC available
# before any admin exists. DB rows take precedence on slug conflict.
#
# Flow:
#   1. Browser hits /auth/oidc/<slug>/login
#        → server 302-redirects to IdP authorization URL with state+PKCE
#   2. User authenticates at IdP
#   3. IdP redirects back to /auth/oidc/<slug>/callback?code=&state=
#        → server exchanges code for tokens, validates the id_token,
#          resolves the local user (or auto-provisions on bootstrap),
#          sets the session cookie, and 302-redirects to /
#
# Identity matching policy:
#   - If no users exist (env-var-configured provider in a fresh deploy),
#     the first OIDC sign-in becomes admin. Once any admin exists, this
#     path is unreachable.
#   - Otherwise we require a pre-existing local row matched by email
#     (case-insensitive) or username. Anyone signing in via OIDC without
#     a matching row gets a friendly "ask an admin to invite you" error
#     redirect. Invite-only by default.
# =====================================================================

@router.get("/oidc/{provider_name}/login")
def oidc_login(
    provider_name: str,
    request: Request,
    db: DBSession = Depends(get_db),
):
    provider = oidc.get_provider(db, provider_name)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"unknown provider {provider_name}")
    redirect_uri = _oidc_redirect_uri(request, provider_name)
    url = oidc.begin_login(provider, redirect_uri)
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=url, status_code=302)


@router.get("/oidc/{provider_name}/callback")
def oidc_callback(
    provider_name: str,
    request: Request,
    db: DBSession = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """Receives the IdP's redirect after user auth.

    On any failure (IdP rejected, state mismatch, etc.), we redirect to
    `/login?oidc_error=...` so the SPA can surface the message — much
    friendlier than serving a JSON blob in the browser.
    """
    from fastapi.responses import RedirectResponse
    if error:
        return _oidc_error_redirect(error_description or error)
    if not code or not state:
        return _oidc_error_redirect("missing code or state from IdP")

    try:
        claims = oidc.complete_login(db, state, code)
    except Exception as exc:  # noqa: BLE001
        # F-5: don't reflect raw exception text into the redirect URL —
        # it can carry token-endpoint URLs / httpx internals into the
        # user's history, and is a reflected-XSS sink if the SPA ever
        # rendered it unescaped. Log the detail server-side; return a
        # generic message.
        import logging
        logging.getLogger(__name__).warning(
            "OIDC complete_login failed for provider=%s: %s", provider_name, exc
        )
        return _oidc_error_redirect(
            "OIDC sign-in failed. Contact your administrator if it persists."
        )

    sub = claims.get("sub")
    email = (claims.get("email") or "").strip().lower()
    # AppSec F-1(a): an `email` claim is only trustworthy when the IdP
    # asserts it verified. Treat absent-or-false identically — both fail
    # the gate. Multi-tenant / consumer IdPs let a principal assert an
    # email it doesn't own; matching a pre-invited row on an unverified
    # email is account takeover (incl. admin). `email_verified` may
    # arrive as a real bool or, from some IdPs, the string "true".
    ev = claims.get("email_verified")
    email_verified = ev is True or (isinstance(ev, str) and ev.lower() == "true")
    verified_email = email if (email and email_verified) else ""
    preferred = claims.get("preferred_username") or claims.get("nickname")
    if not sub:
        return _oidc_error_redirect("IdP response missing sub claim")

    # Log every callback's identifying claims. Surfacing what we actually
    # got makes mismatches trivial to diagnose (Entra returns email only
    # when an optional claim is enabled; verification varies by IdP).
    import logging
    logging.getLogger(__name__).info(
        "OIDC callback claims provider=%s sub=%s email=%r email_verified=%s "
        "preferred_username=%r name=%r",
        provider_name, sub, email, email_verified, preferred, claims.get("name"),
    )

    # AppSec F-1(b): identity is keyed on the durable (provider, sub),
    # NOT on mutable email / UPN. We match that FIRST; it's set the first
    # time a user links, so a reused UPN or changed email can never
    # silently hijack an existing account.
    user = (
        db.query(User)
        .filter(User.oidc_provider == provider_name, User.oidc_sub == sub)
        .first()
    )

    if user is None:
        # AppSec F-7: OIDC is strictly invite-only — it NEVER bootstraps
        # an admin. The first admin is created locally via
        # /auth/signup-first; a provider can only be configured by an
        # existing admin, so "first OIDC sign-in becomes admin" was both
        # unreachable in normal flow and a latent footgun. Removed.
        #
        # Invite-only INITIAL link: the only attribute we'll auto-link on
        # is a VERIFIED email matching a pre-invited row — never an
        # unverified email and never a bare username/UPN (both are
        # attacker-controllable on an untrusted IdP). Once linked, the
        # (provider, sub) branch above handles all future logins.
        user = None
        if verified_email:
            user = db.query(User).filter(User.email == verified_email).first()
        if user is None or not user.is_active:
            # Distinguish the two failure modes so the operator can act.
            # Only ever echo the user's OWN claims back to them.
            if email and not email_verified:
                return _oidc_error_redirect(
                    "your IdP did not assert email_verified for "
                    f"{email}, so we won't link an account on it. "
                    "Ask an admin to enable the verified-email claim "
                    "on the IdP, or sign in with username + password."
                )
            hint = f" (email={verified_email})" if verified_email else ""
            return _oidc_error_redirect(
                f"no active account matches this identity{hint}. "
                "Ask an admin to invite you (by email)."
            )
        # First successful link — bind the durable identity so future
        # logins don't depend on email at all.
        user.oidc_provider = provider_name
        user.oidc_sub = sub
        db.commit()
        db.refresh(user)
    elif not user.is_active:
        return _oidc_error_redirect("your account is disabled. Ask an admin.")

    # Issue a session cookie and bounce to the SPA.
    token = create_session(db, user, user_agent=request.headers.get("user-agent"))
    resp = RedirectResponse(url="/", status_code=302)
    _set_session_cookie(resp, token)
    return resp


def _oidc_redirect_uri(request: Request, provider_name: str) -> str:
    """The callback URL we hand the IdP at the start of the flow. The
    same URL must reach this endpoint at the end of it — IdPs strict-
    match registered redirect URIs. Configure via PUBLIC_BASE_URL when
    behind a reverse proxy; falls back to the request's host otherwise.
    """
    base = get_settings().PUBLIC_BASE_URL.rstrip("/")
    if base:
        return f"{base}/api/auth/oidc/{provider_name}/callback"
    # Fallback for dev. The behind-an-ingress case really wants PUBLIC_BASE_URL.
    return str(request.url_for("oidc_callback", provider_name=provider_name))


def _oidc_error_redirect(message: str):
    """Redirect to the SPA's /login route with the error in a query param.
    The SPA surfaces it on the page."""
    from fastapi.responses import RedirectResponse
    from urllib.parse import quote
    return RedirectResponse(url=f"/login?oidc_error={quote(message)}", status_code=302)


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
