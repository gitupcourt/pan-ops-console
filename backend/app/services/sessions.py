"""Session token creation, lookup, and revocation.

The cookie carries a 256-bit random token. The DB only stores its SHA-256
hash — leaking the sessions table doesn't hand an attacker active session
cookies. This is the same pattern Django, Rails, etc. use.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session as DBSession

from app.config import get_settings
from app.models.user import Session, User


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def create_session(
    db: DBSession,
    user: User,
    *,
    user_agent: str | None = None,
) -> str:
    """Create a new session row and return the raw token (the cookie value).
    The caller is responsible for setting it on the response."""
    token = secrets.token_urlsafe(48)  # 64 chars after base64 encoding
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=settings.SESSION_LIFETIME_SECONDS)

    db.add(Session(
        token_hash=_hash_token(token),
        user_id=user.id,
        created_at=now,
        expires_at=expires,
        last_seen_at=now,
        user_agent=(user_agent or "")[:500] or None,
    ))
    user.last_login_at = now
    db.commit()
    return token


def lookup_session(db: DBSession, token: str | None) -> Session | None:
    """Return the Session row if `token` is valid and not expired.

    Bumps `last_seen_at` as a side effect (sliding-window staleness, not
    sliding-window expiry — we don't extend expires_at).
    """
    if not token:
        return None
    row = db.get(Session, _hash_token(token))
    if row is None:
        return None
    now = datetime.now(timezone.utc)
    # SQLite drops tzinfo; restore for comparison.
    exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if exp < now:
        db.delete(row)
        db.commit()
        return None
    if not row.user.is_active:
        return None
    row.last_seen_at = now
    db.commit()
    return row


def revoke_session(db: DBSession, token: str) -> None:
    row = db.get(Session, _hash_token(token))
    if row is not None:
        db.delete(row)
        db.commit()


def revoke_all_for_user(db: DBSession, user_id: int) -> int:
    """Delete every session for `user_id`. Used by "sign out everywhere"
    and by `deactivate user`. Returns the number of sessions killed.
    """
    n = db.query(Session).filter(Session.user_id == user_id).delete()
    db.commit()
    return n
