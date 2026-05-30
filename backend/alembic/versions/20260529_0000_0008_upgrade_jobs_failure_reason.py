"""upgrade_jobs: add failure_reason column

A job goes FAILED for one of two reasons:

  1. A per-device task failed inside a phase function — that task's
     `device_upgrade_tasks.error` holds the detail, and the UI already
     shows it on the task row.
  2. The orchestrator crashed OUTSIDE any phase function — e.g. the
     `drive_pair` outer except handler calling
     `_fail_job(job_id, "driver crashed; see worker logs")`, or a
     confirmation/override timeout. In this case NO task is marked
     FAILED, so there's no per-task error to show and the job header
     read a bare "FAILED" with no explanation.

`_fail_job` already accepts a `reason` argument but, before this
migration, discarded it. This adds a nullable Text column to persist
it (first-write-wins, so the root cause isn't overwritten by a later
cascade), surfaced on JobRead so the JobDetail header can render
"why did this fail?".

Plain nullable column — no enum, no FK — so a raw ALTER works on
Postgres. Wrapped in batch_alter_table for SQLite parity (matches the
proven pattern in migrations 0002 / 0007).

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-29 00:00:00 UTC
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("upgrade_jobs") as batch:
        batch.add_column(
            sa.Column("failure_reason", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("upgrade_jobs") as batch:
        batch.drop_column("failure_reason")
