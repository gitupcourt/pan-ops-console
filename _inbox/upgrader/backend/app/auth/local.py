"""Local username/password authentication backend."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.auth.passwords import verify_password
from app.models.user import User


def authenticate_local(db: Session, username: str, password: str) -> User | None:
    """Return the User if credentials match a local account, else None."""
    user = (
        db.query(User)
        .filter(User.username == username, User.auth_provider == "local", User.is_active.is_(True))
        .one_or_none()
    )
    if user is None or user.password_hash is None:
        return None
    if not verify_password(password, user.password_hash):
        return None

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return user
