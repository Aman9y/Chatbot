"""Cycle-as-spine persisted state: interest, country decision, Rafique intro

Adds leads.considering_abroad (nullable bool), leads.country_still_deciding
(bool, default false) and leads.rafique_introduced (bool, default false).
Director review: the qualification cycle (interest -> eligibility -> country
-> Rafique Sir) is tracked as real, persistent lead state, computed every
turn from actual message history — see
app/services/conversation/context.py and extraction.py — so a completed step
is never re-asked and survives side conversations intact.

Revision ID: e6c2b9a41f07
Revises: d1a7c4e2f8b3
Create Date: 2026-09-12 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'e6c2b9a41f07'
down_revision: str | None = 'd1a7c4e2f8b3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.add_column(sa.Column('considering_abroad', sa.Boolean(), nullable=True))
        batch_op.add_column(
            sa.Column(
                'country_still_deciding', sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch_op.add_column(
            sa.Column(
                'rafique_introduced', sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.drop_column('rafique_introduced')
        batch_op.drop_column('country_still_deciding')
        batch_op.drop_column('considering_abroad')
