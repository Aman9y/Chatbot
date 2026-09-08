"""conversation_traces.turn_signals (behaviour layer)

Records the per-turn pace / tone-stage / cta-mode / topic-handling / objection
that shaped the reply, for prompt tuning and "why did the bot say X" audits.

Revision ID: b7e2a9c14d55
Revises: a1c4f7d2e880
Create Date: 2026-09-08 14:05:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'b7e2a9c14d55'
down_revision: str | None = 'a1c4f7d2e880'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('conversation_traces', schema=None) as batch_op:
        batch_op.add_column(sa.Column('turn_signals', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('conversation_traces', schema=None) as batch_op:
        batch_op.drop_column('turn_signals')
