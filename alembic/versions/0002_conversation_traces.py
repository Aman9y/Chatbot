"""conversation traces + handoffs + financing_cleared (Phase 3/4)

Adds conversation_traces (decision trail per inbound turn), handoff_notifications
(counsellor queue), and leads.financing_cleared. Portable Postgres/SQLite.

Revision ID: 0f0e1cd8d6ec
Revises: 473dfb3ffc2d
Create Date: 2026-09-08 09:37:50.414526
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0f0e1cd8d6ec'
down_revision: str | None = '473dfb3ffc2d'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('handoff_notifications',
    sa.Column('lead_id', sa.Uuid(), nullable=False),
    sa.Column('trigger', sa.Enum('booking', 'phase_handoff', 'guard_fallback', 'engine_error', 'manual', name='handofftrigger', native_enum=False, length=48), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('context', sa.JSON(), nullable=True),
    sa.Column('delivered', sa.Boolean(), nullable=False),
    sa.Column('delivered_at', sa.DateTime(), nullable=True),
    sa.Column('delivery_channel', sa.String(length=40), nullable=True),
    sa.Column('delivery_error', sa.Text(), nullable=True),
    sa.Column('acknowledged_at', sa.DateTime(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_handoff_notifications_lead_id_leads'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_handoff_notifications'))
    )
    with op.batch_alter_table('handoff_notifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_handoff_notifications_delivered'), ['delivered'], unique=False)
        batch_op.create_index(batch_op.f('ix_handoff_notifications_lead_id'), ['lead_id'], unique=False)

    op.create_table('conversation_traces',
    sa.Column('lead_id', sa.Uuid(), nullable=False),
    sa.Column('inbound_message_id', sa.Uuid(), nullable=True),
    sa.Column('outbound_message_id', sa.Uuid(), nullable=True),
    sa.Column('speaker_detected', sa.Enum('student', 'parent', 'unknown', name='rolehint', native_enum=False, length=48), nullable=False),
    sa.Column('speaker_method', sa.String(length=32), nullable=True),
    sa.Column('engagement_phase', sa.String(length=24), nullable=True),
    sa.Column('lifecycle_state', sa.String(length=24), nullable=True),
    sa.Column('kb_chunk_ids', sa.JSON(), nullable=True),
    sa.Column('llm_provider', sa.String(length=24), nullable=True),
    sa.Column('llm_model', sa.String(length=64), nullable=True),
    sa.Column('llm_input_tokens', sa.Integer(), nullable=False),
    sa.Column('llm_output_tokens', sa.Integer(), nullable=False),
    sa.Column('llm_latency_ms', sa.Numeric(precision=10, scale=2), nullable=True),
    sa.Column('llm_calls', sa.Integer(), nullable=False),
    sa.Column('draft_attempts', sa.Integer(), nullable=False),
    sa.Column('drafts', sa.JSON(), nullable=True),
    sa.Column('guard_verdict', sa.String(length=32), nullable=True),
    sa.Column('guard_violations', sa.JSON(), nullable=True),
    sa.Column('final_action', sa.String(length=32), nullable=False),
    sa.Column('final_text', sa.Text(), nullable=True),
    sa.Column('skipped_reason', sa.String(length=64), nullable=True),
    sa.Column('booking_detected', sa.Boolean(), nullable=False),
    sa.Column('booking_details', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('cost_amount', sa.Numeric(precision=12, scale=6), nullable=True),
    sa.Column('cost_currency', sa.String(length=8), nullable=True),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['inbound_message_id'], ['messages.id'], name=op.f('fk_conversation_traces_inbound_message_id_messages'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_conversation_traces_lead_id_leads'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['outbound_message_id'], ['messages.id'], name=op.f('fk_conversation_traces_outbound_message_id_messages'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversation_traces'))
    )
    with op.batch_alter_table('conversation_traces', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_conversation_traces_inbound_message_id'), ['inbound_message_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_conversation_traces_lead_id'), ['lead_id'], unique=False)

    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'financing_cleared',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.drop_column('financing_cleared')

    with op.batch_alter_table('conversation_traces', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_conversation_traces_lead_id'))
        batch_op.drop_index(batch_op.f('ix_conversation_traces_inbound_message_id'))

    op.drop_table('conversation_traces')
    with op.batch_alter_table('handoff_notifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_handoff_notifications_lead_id'))
        batch_op.drop_index(batch_op.f('ix_handoff_notifications_delivered'))

    op.drop_table('handoff_notifications')
