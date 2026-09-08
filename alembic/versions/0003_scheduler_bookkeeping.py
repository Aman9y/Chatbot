"""scheduler bookkeeping columns (Phase 5 + 6)

Adds leads.{dormant_at, last_nudge_at, nudge_count, phase_handoff_notified,
next_reengagement_at, last_reengagement_at, nurture_round} for the re-engagement
scheduler. Portable Postgres/SQLite.

Revision ID: 5e889aa60e9b
Revises: 0f0e1cd8d6ec
Create Date: 2026-09-08 10:25:46.205356
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '5e889aa60e9b'
down_revision: str | None = '0f0e1cd8d6ec'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.add_column(sa.Column('dormant_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('last_nudge_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('nudge_count', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('phase_handoff_notified', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('next_reengagement_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('last_reengagement_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('nurture_round', sa.Integer(), nullable=False, server_default='0'))
        batch_op.create_index(batch_op.f('ix_leads_next_reengagement_at'), ['next_reengagement_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_leads_next_reengagement_at'))
        batch_op.drop_column('nurture_round')
        batch_op.drop_column('last_reengagement_at')
        batch_op.drop_column('next_reengagement_at')
        batch_op.drop_column('phase_handoff_notified')
        batch_op.drop_column('nudge_count')
        batch_op.drop_column('last_nudge_at')
        batch_op.drop_column('dormant_at')

