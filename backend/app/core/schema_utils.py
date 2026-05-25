"""Shared Pydantic schema helpers.

Kept tiny — anything that crosses module boundaries (e.g. a UTC coercion
field validator) lives here so modules don't import each other's schema
files just for utilities.
"""

from datetime import datetime, timezone


def ensure_utc(dt: datetime | None) -> datetime | None:
    """SQLite drops the timezone on datetimes even when the column is
    declared `DateTime(timezone=True)`. Everything in the DB is *written*
    as UTC, so when we read it back naive we reattach UTC. Without this
    the JSON serializer emits ISO strings with no offset, and the
    browser's `new Date(...)` interprets them as LOCAL time — making
    every chart appear empty because the points land in the future
    relative to `now()`.
    """
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)
