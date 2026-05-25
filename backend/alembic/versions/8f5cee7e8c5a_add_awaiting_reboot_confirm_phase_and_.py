"""add awaiting_reboot_confirm phase and auto_reboot column

Revision ID: 8f5cee7e8c5a
Revises: 9b3e73274535
Create Date: 2026-05-15 02:24:48.519131

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8f5cee7e8c5a'
down_revision: Union[str, None] = '9b3e73274535'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ADD VALUE has to run outside a tx — see migration
    # 9b3e73274535 for the same dance.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE task_phase ADD VALUE IF NOT EXISTS 'AWAITING_REBOOT_CONFIRM'")

    # New job-level flag. server_default=false populates existing rows
    # cleanly; then we drop the default so future inserts honor the
    # application-level default the model declares.
    op.add_column(
        'upgrade_jobs',
        sa.Column('auto_reboot_after_install', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column('upgrade_jobs', 'auto_reboot_after_install', server_default=None)


def downgrade() -> None:
    op.drop_column('upgrade_jobs', 'auto_reboot_after_install')
    # PostgreSQL doesn't drop enum values cleanly; left as a no-op.
