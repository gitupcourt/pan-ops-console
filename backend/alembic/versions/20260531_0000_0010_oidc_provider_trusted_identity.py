"""oidc_providers: add trusted_identity flag

Per-provider opt-in (default False) that lets the OIDC callback link a
new (provider, sub) to a pre-invited local account by matching the IdP's
asserted email / UPN / preferred_username, even when the IdP does not
send `email_verified`.

Motivation: Microsoft Entra ID never emits `email_verified` (and often
no `email` claim at all — the address arrives in `preferred_username`/
`upn`). The F-1 hardening (migration 0009) only auto-links on a VERIFIED
email, so no Entra user could ever onboard. This flag lets an operator
mark an IdP they control as trusted, restoring onboarding for it while
keeping F-1's protection (verified-email-only) as the default for
untrusted / multi-tenant IdPs.

Additive, NOT NULL with server_default false so existing rows get the
safe (untrusted) value. batch_alter_table for SQLite parity.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-31 00:00:00 UTC
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("oidc_providers") as batch:
        batch.add_column(
            sa.Column(
                "trusted_identity",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("oidc_providers") as batch:
        batch.drop_column("trusted_identity")
