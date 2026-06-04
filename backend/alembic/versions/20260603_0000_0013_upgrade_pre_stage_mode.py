"""upgrade_jobs: add pre_stage_mode for stage-only jobs

A job runs as a full upgrade (default) or stage-only: precheck + image download
+ pre-snapshot, then STOP before install/reboot — for pre-staging a fleet ahead
of a maintenance window. Stored as a plain string (not a DB enum) so future
modes (e.g. "hold") are additive without an ALTER TYPE. Existing rows backfill
to "none" via the server_default.

Additive only; batch_alter_table for SQLite parity.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-03 00:00:00 UTC
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("upgrade_jobs") as batch:
        batch.add_column(
            sa.Column(
                "pre_stage_mode",
                sa.String(length=16),
                nullable=False,
                server_default="none",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("upgrade_jobs") as batch:
        batch.drop_column("pre_stage_mode")
