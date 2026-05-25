"""add precheck and postcheck override phases

Revision ID: 9b3e73274535
Revises: 635e05884c54
Create Date: 2026-05-14 18:02:52.558767

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9b3e73274535'
down_revision: Union[str, None] = '635e05884c54'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL stores enum values as the SQLAlchemy Python enum NAME
    # (uppercase), not the .value.
    #
    # ALTER TYPE ... ADD VALUE only works *outside* an explicit transaction
    # in some PG versions, and Alembic runs migrations in a transaction by
    # default. Use autocommit_block() to issue these as standalone statements.
    # IF NOT EXISTS makes reruns safe.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE task_phase ADD VALUE IF NOT EXISTS 'AWAITING_PRECHECK_OVERRIDE'")
        op.execute("ALTER TYPE task_phase ADD VALUE IF NOT EXISTS 'AWAITING_POSTCHECK_OVERRIDE'")


def downgrade() -> None:
    # PostgreSQL doesn't support dropping enum values cleanly. A real
    # downgrade would require recreating the type and migrating data.
    # Left as a no-op; recreate the DB volume if you really need to revert.
    pass
