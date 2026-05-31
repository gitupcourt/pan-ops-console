"""users: add oidc_provider + oidc_sub for durable identity binding

AppSec F-1 (HIGH). OIDC's stable identifier is (issuer, sub). The
callback previously matched a local user on the mutable `email` /
`preferred_username` claims and never persisted `sub`, which (a) let an
attacker assert a pre-invited user's email on an IdP that doesn't
verify it → account takeover, and (b) silently reassigned an account
when a UPN/email was reused.

This migration adds the two nullable columns the fix matches on first.
`sub` is unique only within an issuer, so we store the provider
alongside it. Plain index for lookup; uniqueness of (provider, sub) is
enforced in app logic at link time (a nullable composite unique behaves
inconsistently across SQLite/Postgres, and we want to allow many rows
with both NULL).

Both columns are NULL for existing rows — local-only users and
not-yet-linked invitees. They get populated on the next successful
OIDC link.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-29 01:00:00 UTC
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("oidc_provider", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("oidc_sub", sa.String(length=255), nullable=True))
    op.create_index(
        "ix_users_oidc_provider_sub",
        "users",
        ["oidc_provider", "oidc_sub"],
    )


def downgrade() -> None:
    op.drop_index("ix_users_oidc_provider_sub", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("oidc_sub")
        batch.drop_column("oidc_provider")
