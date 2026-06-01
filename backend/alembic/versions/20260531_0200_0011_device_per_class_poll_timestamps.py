"""devices: add per-class poll timestamps for the staggered dispatcher

The capacity dispatcher (#89) polls system-class metrics on a fast cadence
and config-class metrics on a slow one, deciding per-device what's "due."
That needs a poll timestamp PER class, not the single device-level
`last_poll_at`. Adds two nullable columns; existing rows start NULL, which
the dispatcher treats as "due now" (with cold-start jitter so a fresh
deploy doesn't stampede). `last_poll_at` is retained as "most recent poll
of any class" for the Inventory UI.

Additive only; batch_alter_table for SQLite parity.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-31 02:00:00 UTC
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(
            sa.Column("last_system_poll_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column("last_config_poll_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("last_config_poll_at")
        batch.drop_column("last_system_poll_at")
