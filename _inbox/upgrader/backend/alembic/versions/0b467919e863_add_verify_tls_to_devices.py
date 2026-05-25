"""add verify_tls to devices

Revision ID: 0b467919e863
Revises: c4e264510ef5
Create Date: 2026-05-08 21:43:32.639271

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0b467919e863'
down_revision: Union[str, None] = 'c4e264510ef5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add with a server-side default so existing rows pick up True; then drop the
    # default so future inserts use the application-level default.
    op.add_column(
        'devices',
        sa.Column('verify_tls', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column('devices', 'verify_tls', server_default=None)


def downgrade() -> None:
    op.drop_column('devices', 'verify_tls')
