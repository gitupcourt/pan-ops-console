"""add proxy_via_panorama to devices

Revision ID: 3cebc2969270
Revises: 32de244f9ab1
Create Date: 2026-05-08 22:11:00.299369

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3cebc2969270'
down_revision: Union[str, None] = '32de244f9ab1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'devices',
        sa.Column('proxy_via_panorama', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column('devices', 'proxy_via_panorama', server_default=None)


def downgrade() -> None:
    op.drop_column('devices', 'proxy_via_panorama')
