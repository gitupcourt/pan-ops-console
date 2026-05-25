"""add panorama reachability tracking

Revision ID: 97b0410a856e
Revises: 8f5cee7e8c5a
Create Date: 2026-05-15 03:10:04.004513

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '97b0410a856e'
down_revision: Union[str, None] = '8f5cee7e8c5a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'panoramas',
        sa.Column('reachable', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column('panoramas', 'reachable', server_default=None)

    op.add_column(
        'panoramas',
        sa.Column('last_reachability_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'panoramas',
        sa.Column('last_reachability_error', sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('panoramas', 'last_reachability_error')
    op.drop_column('panoramas', 'last_reachability_at')
    op.drop_column('panoramas', 'reachable')
