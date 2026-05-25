"""Shared SQLAlchemy column type helpers.

Centralizes the column-type quirks that need to be applied consistently
across every model, so we don't end up with one Enum column doing the
right thing and another silently mismatched.
"""

from __future__ import annotations

from enum import Enum as PyEnum
from typing import Type

from sqlalchemy import Enum as SAEnum


def py_enum_column(py_enum: Type[PyEnum], *, name: str) -> SAEnum:
    """Build a SQLAlchemy Enum column that uses the Python enum member's
    `.value` (not the default `.name`) as the on-wire representation.

    Why this exists (pan-ops-console#41 — caught at phase 2e cutover):

    SQLAlchemy's default Enum behavior writes/reads the enum **name**
    (`DeviceSource.PANORAMA.name == "PANORAMA"`). Our alembic migrations
    create Postgres ENUM types using the enum's **value**
    (`DeviceSource.PANORAMA.value == "panorama"`) — they hard-code
    lowercase strings. Default behavior + lowercase-typed Postgres ENUM
    = the ORM writes/reads `"PANORAMA"` against a type that only accepts
    `"panorama"`. Postgres throws on read; SQLite silently lies about
    the CHECK constraint.

    `values_callable=lambda e: [m.value for m in e]` tells SQLAlchemy to
    use `.value` for both the wire format and the type-emission, so the
    ORM and the Postgres ENUM type agree on case.

    Use for every model column that takes a Python enum. The matching
    discipline is documented in CLAUDE.md.

    Usage:

        from app.core.db_types import py_enum_column

        class Device(Base):
            source: Mapped[DeviceSource] = mapped_column(
                py_enum_column(DeviceSource, name="device_source"),
                ...
            )
    """
    return SAEnum(
        py_enum,
        name=name,
        values_callable=lambda enums: [m.value for m in enums],
    )
