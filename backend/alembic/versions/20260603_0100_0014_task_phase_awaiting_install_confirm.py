"""task_phase: add AWAITING_INSTALL_CONFIRM for the "Stage & hold" gate

Adds a new value to the `task_phase` enum so a stage-and-hold job can park
before install awaiting "Proceed to install".

Dialect-aware:
  - Postgres: `task_phase` is a native ENUM type, so the value must be added
    with `ALTER TYPE ... ADD VALUE`. That statement cannot run inside the
    migration's transaction, so it goes in an autocommit block. IF NOT EXISTS
    makes it idempotent (PG12+).
  - SQLite (tests): `py_enum_column` emits a plain VARCHAR (SQLAlchemy 2.0
    defaults Enum.create_constraint=False — no CHECK), so a new value needs no
    DDL at all. No-op.

Downgrade is a no-op: Postgres can't drop an enum value without recreating the
type, and nothing depends on its removal.

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-03 01:00:00 UTC
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # ALTER TYPE ... ADD VALUE must run outside the surrounding transaction.
        with op.get_context().autocommit_block():
            op.execute(
                "ALTER TYPE task_phase ADD VALUE IF NOT EXISTS 'awaiting_install_confirm'"
            )
    # SQLite: task_phase is a plain VARCHAR (no CHECK) — nothing to do.


def downgrade() -> None:
    # Removing a Postgres enum value requires recreating the type; not worth it
    # and nothing depends on the removal. No-op.
    pass
