"""job_state: add COMPLETED_WITH_ERRORS for per-device failure isolation

Adds a new value to the `job_state` enum so a multi-device job where some
devices succeed and some fail lands in a clear "completed with errors" terminal
state instead of a blanket FAILED — a single device's failure no longer fails
the whole job.

Dialect-aware (same shape as migration 0014):
  - Postgres: `job_state` is a native ENUM type, so the value is added with
    `ALTER TYPE ... ADD VALUE`, which cannot run inside the migration's
    transaction — hence the autocommit block. IF NOT EXISTS makes it idempotent
    (PG12+).
  - SQLite (tests): `py_enum_column` emits a plain VARCHAR (SQLAlchemy 2.0
    defaults Enum.create_constraint=False — no CHECK), so a new value needs no
    DDL. No-op.

Downgrade is a no-op: Postgres can't drop an enum value without recreating the
type, and nothing depends on its removal.

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-04 00:00:00 UTC
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # ALTER TYPE ... ADD VALUE must run outside the surrounding transaction.
        with op.get_context().autocommit_block():
            op.execute(
                "ALTER TYPE job_state ADD VALUE IF NOT EXISTS 'completed_with_errors'"
            )
    # SQLite: job_state is a plain VARCHAR (no CHECK) — nothing to do.


def downgrade() -> None:
    # Removing a Postgres enum value requires recreating the type; not worth it
    # and nothing depends on the removal. No-op.
    pass
