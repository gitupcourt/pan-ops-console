"""User-defined named subsets of readiness checks.

The library + our classifier define ~18 readiness checks. Most operators
care about a smaller, opinionated subset — and that subset varies by team
(infosec wants license + cert checks; netops cares about HA + content).
Rather than baking a single default in the UI, we let users save named
presets they can pick from the Pre-check menu.

We never validate that the names in `checks` are in ALL_READINESS_CHECKS
at write time — the library's check list can grow between releases, and
we'd rather quietly skip an unknown check name (the precheck service
already handles that) than refuse a save.
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PrecheckSet(Base):
    """A named, ordered subset of readiness check names."""

    __tablename__ = "precheck_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # List of check names. Stored as JSON for trivial round-tripping; order
    # is preserved so the operator sees them in their preferred reading order.
    checks: Mapped[list] = mapped_column(JSON, nullable=False)
    # When True, this set is offered as the "default" pick in the UI. Only
    # one set should have is_default=True at a time; we enforce that in the
    # CRUD endpoint, not at the schema level (Postgres partial unique index
    # would be overkill for the case where one user wants to toggle between
    # two "default" candidates).
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
