"""First-run bootstrap: create the initial admin if no users exist."""

import logging

from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.config import get_settings
from app.models.user import User

log = logging.getLogger(__name__)


def ensure_initial_admin(db: Session) -> None:
    if db.query(User).count() > 0:
        return

    settings = get_settings()
    admin = User(
        username=settings.INITIAL_ADMIN_USERNAME,
        password_hash=hash_password(settings.INITIAL_ADMIN_PASSWORD),
        is_admin=True,
        is_active=True,
        auth_provider="local",
    )
    db.add(admin)
    db.commit()
    log.warning(
        "Created initial admin user '%s'. CHANGE THIS PASSWORD IMMEDIATELY.",
        settings.INITIAL_ADMIN_USERNAME,
    )
