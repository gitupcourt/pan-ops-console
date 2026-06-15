"""panorama_sw_version: store the Panorama's own running PAN-OS version

Adds `panoramas.sw_version`, captured from `show system info` whenever we reach
the Panorama (scheduled sync + the Test-connection handler). Surfaced on the
Inventory → Panoramas table so operators can see each Panorama's running PAN-OS
version, the same way the Devices table shows each firewall's version.

Additive + nullable — backfills naturally on the next sync or Test, no data
migration needed.

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-15 00:00:00 UTC
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "panoramas",
        sa.Column("sw_version", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("panoramas", "sw_version")
