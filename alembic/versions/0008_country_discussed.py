"""Enforced country-discussion gate before any Rafique Sir CTA (director review)

Adds leads.country_discussed (boolean, default false, sticky). This is real,
persistent lead state — computed every turn from the actual message history
(see app/services/conversation/context.py) and enforced by the Response Guard
(app/services/guard/guard.py's `_check_premature_contact_offer`), not just a
prompt instruction: a draft that mentions a call / the director's number /
the office while this flag is still false is blocked and regenerated exactly
like an out-of-range cost figure.

Revision ID: d1a7c4e2f8b3
Revises: 8b3f6a1c9d02
Create Date: 2026-09-12 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'd1a7c4e2f8b3'
down_revision: str | None = '8b3f6a1c9d02'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'country_discussed', sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.drop_column('country_discussed')
