"""conversational opt-in + age gate (build-plan §2 / DPDP)

Adds leads.{consent_gate, gate_reask_count, consent_ask_sent_at,
consent_ask_count}. New lifecycle state 'gate_hold' + event 'gate_held' need no
DDL (enum columns are plain VARCHAR, validated in Python).

Revision ID: c9f1e05a7b62
Revises: b7e2a9c14d55
Create Date: 2026-09-08 15:20:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'c9f1e05a7b62'
down_revision: str | None = 'b7e2a9c14d55'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GATE = sa.Enum(
    'pending_opt_in', 'pending_age', 'cleared', 'refused', 'minor_hold',
    'needs_human', 'not_required',
    name='consentgate', native_enum=False, length=48,
)


def upgrade() -> None:
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('consent_gate', _GATE, nullable=False, server_default='pending_opt_in')
        )
        batch_op.add_column(
            sa.Column('gate_reask_count', sa.Integer(), nullable=False, server_default='0')
        )
        batch_op.add_column(sa.Column('consent_ask_sent_at', sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column('consent_ask_count', sa.Integer(), nullable=False, server_default='0')
        )
        batch_op.create_index(batch_op.f('ix_leads_consent_gate'), ['consent_gate'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_leads_consent_gate'))
        batch_op.drop_column('consent_ask_count')
        batch_op.drop_column('consent_ask_sent_at')
        batch_op.drop_column('gate_reask_count')
        batch_op.drop_column('consent_gate')
