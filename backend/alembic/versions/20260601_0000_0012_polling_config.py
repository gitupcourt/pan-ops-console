"""polling_config: runtime-editable polling tunables (#89 PR-4)

Singleton table (one row, id=1) backing Settings → Polling, so cadences +
per-Panorama concurrency are editable at runtime without a redeploy. The
dispatcher reads it each tick (DB row wins over config.py env defaults).

Seeds the single row from the shipped defaults (system 300s, config 12h,
per-Panorama cap 4, retry backoff 60s) so the resolver always finds it.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-01 00:00:00 UTC
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "polling_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("system_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("config_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("max_concurrency_per_panorama", sa.Integer(), nullable=False),
        sa.Column("device_retry_backoff_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # Seed the singleton from the shipped config.py defaults. updated_at fills
    # from its server_default.
    op.execute(
        "INSERT INTO polling_config "
        "(id, system_interval_seconds, config_interval_seconds, "
        "max_concurrency_per_panorama, device_retry_backoff_seconds) "
        "VALUES (1, 300, 43200, 4, 60)"
    )


def downgrade() -> None:
    op.drop_table("polling_config")
