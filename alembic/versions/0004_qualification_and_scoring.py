"""in-conversation qualification + lead scoring (build-plan §3 / §4, plan §3)

Adds:
  leads.{urgency, parent_in_loop, qualifiers_updated_at, lead_score,
         lead_score_reason, lead_score_updated_at, counsellor_cta_sent}
  conversation_traces.{extracted_qualifiers, lead_score, lead_score_reason,
                       interest_temperature}

Portable Postgres/SQLite (batch mode, native_enum=False).

Revision ID: a1c4f7d2e880
Revises: 5e889aa60e9b
Create Date: 2026-09-08 13:10:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'a1c4f7d2e880'
down_revision: str | None = '5e889aa60e9b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_URGENCY = sa.Enum(
    'this_intake', 'next_intake', 'undecided', 'unknown',
    name='leadurgency', native_enum=False, length=48,
)
_SCORE = sa.Enum(
    'unknown', 'low', 'nurture', 'high',
    name='leadscore', native_enum=False, length=48,
)


def upgrade() -> None:
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('urgency', _URGENCY, nullable=False, server_default='unknown')
        )
        batch_op.add_column(
            sa.Column('parent_in_loop', sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column('qualifiers_updated_at', sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column('lead_score', _SCORE, nullable=False, server_default='unknown')
        )
        batch_op.add_column(sa.Column('lead_score_reason', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('lead_score_updated_at', sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column('counsellor_cta_sent', sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_index(batch_op.f('ix_leads_lead_score'), ['lead_score'], unique=False)

    with op.batch_alter_table('conversation_traces', schema=None) as batch_op:
        batch_op.add_column(sa.Column('extracted_qualifiers', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('lead_score', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('lead_score_reason', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('interest_temperature', sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('conversation_traces', schema=None) as batch_op:
        batch_op.drop_column('interest_temperature')
        batch_op.drop_column('lead_score_reason')
        batch_op.drop_column('lead_score')
        batch_op.drop_column('extracted_qualifiers')

    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_leads_lead_score'))
        batch_op.drop_column('counsellor_cta_sent')
        batch_op.drop_column('lead_score_updated_at')
        batch_op.drop_column('lead_score_reason')
        batch_op.drop_column('lead_score')
        batch_op.drop_column('qualifiers_updated_at')
        batch_op.drop_column('parent_in_loop')
        batch_op.drop_column('urgency')
