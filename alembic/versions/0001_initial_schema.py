"""initial schema (Phase 2)

Tables: households, leads, consent_records, messages, webhook_events,
lifecycle_transitions. Portable across PostgreSQL and SQLite; all datetimes
are naive UTC.

Revision ID: 473dfb3ffc2d
Revises: 
Create Date: 2026-09-07 20:53:50.812905
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '473dfb3ffc2d'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('households',
    sa.Column('external_ref', sa.String(length=128), nullable=True),
    sa.Column('label', sa.String(length=255), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_households'))
    )
    with op.batch_alter_table('households', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_households_external_ref'), ['external_ref'], unique=True)

    op.create_table('webhook_events',
    sa.Column('event_hash', sa.String(length=64), nullable=False),
    sa.Column('signature_valid', sa.Boolean(), nullable=False),
    sa.Column('object_type', sa.String(length=40), nullable=True),
    sa.Column('source_ip', sa.String(length=64), nullable=True),
    sa.Column('raw_text', sa.Text(), nullable=True),
    sa.Column('raw_body', sa.JSON(), nullable=True),
    sa.Column('headers', sa.JSON(), nullable=True),
    sa.Column('entry_count', sa.Integer(), nullable=False),
    sa.Column('message_count', sa.Integer(), nullable=False),
    sa.Column('status_count', sa.Integer(), nullable=False),
    sa.Column('processed', sa.Boolean(), nullable=False),
    sa.Column('processed_at', sa.DateTime(), nullable=True),
    sa.Column('processing_error', sa.Text(), nullable=True),
    sa.Column('processing_attempts', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_webhook_events'))
    )
    with op.batch_alter_table('webhook_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_webhook_events_event_hash'), ['event_hash'], unique=True)
        batch_op.create_index(batch_op.f('ix_webhook_events_processed'), ['processed'], unique=False)

    op.create_table('leads',
    sa.Column('household_id', sa.Uuid(), nullable=True),
    sa.Column('phone_e164', sa.String(length=20), nullable=False),
    sa.Column('phone_raw', sa.String(length=64), nullable=True),
    sa.Column('phone_country', sa.String(length=2), nullable=True),
    sa.Column('full_name', sa.String(length=255), nullable=True),
    sa.Column('city', sa.String(length=120), nullable=True),
    sa.Column('language_preference', sa.String(length=32), nullable=True),
    sa.Column('source', sa.String(length=120), nullable=True),
    sa.Column('role_hint', sa.Enum('student', 'parent', 'unknown', name='rolehint', native_enum=False, length=48), nullable=False),
    sa.Column('date_of_birth', sa.Date(), nullable=True),
    sa.Column('age_years', sa.Integer(), nullable=True),
    sa.Column('is_minor', sa.Enum('yes', 'no', 'unknown', name='minorstatus', native_enum=False, length=48), nullable=False),
    sa.Column('minor_policy_status', sa.Enum('not_applicable', 'pending_review', 'cleared_parent_consent', 'blocked', name='minorpolicystatus', native_enum=False, length=48), nullable=False),
    sa.Column('neet_score', sa.Integer(), nullable=True),
    sa.Column('neet_category', sa.Enum('general', 'obc', 'sc', 'st', 'ews', 'unknown', name='neetcategory', native_enum=False, length=48), nullable=False),
    sa.Column('neet_year', sa.Integer(), nullable=True),
    sa.Column('eligibility_flag', sa.Enum('above_cutoff', 'below_cutoff', 'unknown', name='eligibilityflag', native_enum=False, length=48), nullable=False),
    sa.Column('target_country', sa.String(length=80), nullable=True),
    sa.Column('budget_band', sa.String(length=60), nullable=True),
    sa.Column('intake_year', sa.Integer(), nullable=True),
    sa.Column('interest_temperature', sa.Enum('hot', 'mid', 'cold', 'unknown', name='interesttemperature', native_enum=False, length=48), nullable=False),
    sa.Column('consent_status', sa.Enum('unknown', 'opted_in', 'opted_out', 'withdrawn', name='consentstatus', native_enum=False, length=48), nullable=False),
    sa.Column('consent_verified', sa.Boolean(), nullable=False),
    sa.Column('consent_last_source', sa.String(length=255), nullable=True),
    sa.Column('consent_last_evaluated_at', sa.DateTime(), nullable=True),
    sa.Column('opted_out_at', sa.DateTime(), nullable=True),
    sa.Column('lifecycle_state', sa.Enum('never_contacted', 'contacted', 'engaged', 'silent', 'nurture', 'dormant', 'handoff', 'opted_out', name='lifecyclestate', native_enum=False, length=48), nullable=False),
    sa.Column('contacted_at', sa.DateTime(), nullable=True),
    sa.Column('first_engaged_at', sa.DateTime(), nullable=True),
    sa.Column('last_inbound_at', sa.DateTime(), nullable=True),
    sa.Column('last_outbound_at', sa.DateTime(), nullable=True),
    sa.Column('booked_at', sa.DateTime(), nullable=True),
    sa.Column('service_window_expires_at', sa.DateTime(), nullable=True),
    sa.Column('silent_retry_round', sa.Integer(), nullable=False),
    sa.Column('human_owned', sa.Boolean(), nullable=False),
    sa.Column('human_owned_since', sa.DateTime(), nullable=True),
    sa.Column('assigned_counselor', sa.String(length=120), nullable=True),
    sa.Column('no_show', sa.Boolean(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['household_id'], ['households.id'], name=op.f('fk_leads_household_id_households'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leads'))
    )
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_leads_consent_status'), ['consent_status'], unique=False)
        batch_op.create_index(batch_op.f('ix_leads_household_id'), ['household_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_leads_human_owned'), ['human_owned'], unique=False)
        batch_op.create_index(batch_op.f('ix_leads_is_minor'), ['is_minor'], unique=False)
        batch_op.create_index(batch_op.f('ix_leads_lifecycle_state'), ['lifecycle_state'], unique=False)
        batch_op.create_index(batch_op.f('ix_leads_phone_e164'), ['phone_e164'], unique=True)

    op.create_table('lifecycle_transitions',
    sa.Column('lead_id', sa.Uuid(), nullable=False),
    sa.Column('from_state', sa.Enum('never_contacted', 'contacted', 'engaged', 'silent', 'nurture', 'dormant', 'handoff', 'opted_out', name='lifecyclestate', native_enum=False, length=48), nullable=False),
    sa.Column('to_state', sa.Enum('never_contacted', 'contacted', 'engaged', 'silent', 'nurture', 'dormant', 'handoff', 'opted_out', name='lifecyclestate', native_enum=False, length=48), nullable=False),
    sa.Column('event', sa.Enum('outbound_template_sent', 'outbound_message_sent', 'inbound_message', 'booking_confirmed', 'human_takeover', 'human_release', 'service_window_expired', 'nurture_timeout', 'retry_rounds_exhausted', 'opt_out', name='lifecycleevent', native_enum=False, length=48), nullable=False),
    sa.Column('actor', sa.String(length=60), nullable=False),
    sa.Column('reason', sa.String(length=255), nullable=True),
    sa.Column('context', sa.JSON(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_lifecycle_transitions_lead_id_leads'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_lifecycle_transitions'))
    )
    with op.batch_alter_table('lifecycle_transitions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_lifecycle_transitions_lead_id'), ['lead_id'], unique=False)

    op.create_table('messages',
    sa.Column('lead_id', sa.Uuid(), nullable=False),
    sa.Column('direction', sa.Enum('inbound', 'outbound', name='messagedirection', native_enum=False, length=48), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('wa_message_id', sa.String(length=128), nullable=True),
    sa.Column('wa_conversation_id', sa.String(length=128), nullable=True),
    sa.Column('counterparty_phone', sa.String(length=20), nullable=True),
    sa.Column('from_phone', sa.String(length=20), nullable=True),
    sa.Column('to_phone', sa.String(length=20), nullable=True),
    sa.Column('message_type', sa.Enum('text', 'template', 'image', 'video', 'audio', 'document', 'sticker', 'location', 'contacts', 'interactive', 'button', 'reaction', 'order', 'system', 'unsupported', 'unknown', name='messagetype', native_enum=False, length=48), nullable=False),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('template_name', sa.String(length=128), nullable=True),
    sa.Column('template_language', sa.String(length=16), nullable=True),
    sa.Column('template_category', sa.Enum('marketing', 'utility', 'authentication', 'unknown', name='templatecategory', native_enum=False, length=48), nullable=True),
    sa.Column('template_variables', sa.JSON(), nullable=True),
    sa.Column('status', sa.Enum('queued', 'sent', 'delivered', 'read', 'failed', 'received', 'deleted', name='messagestatus', native_enum=False, length=48), nullable=False),
    sa.Column('status_updated_at', sa.DateTime(), nullable=True),
    sa.Column('status_history', sa.JSON(), nullable=False),
    sa.Column('error_code', sa.String(length=40), nullable=True),
    sa.Column('error_title', sa.String(length=255), nullable=True),
    sa.Column('error_detail', sa.Text(), nullable=True),
    sa.Column('error_payload', sa.JSON(), nullable=True),
    sa.Column('pricing_model', sa.String(length=40), nullable=True),
    sa.Column('pricing_category', sa.String(length=40), nullable=True),
    sa.Column('pricing_type', sa.String(length=40), nullable=True),
    sa.Column('billable', sa.Boolean(), nullable=True),
    sa.Column('cost_amount', sa.Numeric(precision=12, scale=5), nullable=True),
    sa.Column('cost_currency', sa.String(length=8), nullable=True),
    sa.Column('sent_by', sa.Enum('bot', 'human', 'system', 'lead', name='sentby', native_enum=False, length=48), nullable=False),
    sa.Column('wa_timestamp', sa.DateTime(), nullable=True),
    sa.Column('raw_payload', sa.JSON(), nullable=True),
    sa.Column('webhook_event_id', sa.Uuid(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=160), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_messages_lead_id_leads'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['webhook_event_id'], ['webhook_events.id'], name=op.f('fk_messages_webhook_event_id_webhook_events'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_messages'))
    )
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_messages_counterparty_phone'), ['counterparty_phone'], unique=False)
        batch_op.create_index(batch_op.f('ix_messages_direction'), ['direction'], unique=False)
        batch_op.create_index(batch_op.f('ix_messages_idempotency_key'), ['idempotency_key'], unique=True)
        batch_op.create_index(batch_op.f('ix_messages_lead_id'), ['lead_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_messages_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_messages_wa_conversation_id'), ['wa_conversation_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_messages_wa_message_id'), ['wa_message_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_messages_wa_timestamp'), ['wa_timestamp'], unique=False)

    op.create_table('consent_records',
    sa.Column('lead_id', sa.Uuid(), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('status', sa.Enum('unknown', 'opted_in', 'opted_out', 'withdrawn', name='consentstatus', native_enum=False, length=48), nullable=False),
    sa.Column('method', sa.Enum('imported_csv_assertion', 'web_form', 'inbound_stop_keyword', 'inbound_opt_in_keyword', 'manual_entry', 'api', name='consentmethod', native_enum=False, length=48), nullable=False),
    sa.Column('verified', sa.Boolean(), nullable=False),
    sa.Column('consent_text', sa.Text(), nullable=True),
    sa.Column('source_reference', sa.String(length=255), nullable=True),
    sa.Column('evidence_locator', sa.String(length=255), nullable=True),
    sa.Column('captured_at', sa.DateTime(), nullable=True),
    sa.Column('actor', sa.String(length=40), nullable=False),
    sa.Column('inbound_message_id', sa.Uuid(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['inbound_message_id'], ['messages.id'], name=op.f('fk_consent_records_inbound_message_id_messages'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], name=op.f('fk_consent_records_lead_id_leads'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_consent_records'))
    )
    with op.batch_alter_table('consent_records', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_consent_records_lead_id'), ['lead_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('consent_records', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_consent_records_lead_id'))

    op.drop_table('consent_records')
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_messages_wa_timestamp'))
        batch_op.drop_index(batch_op.f('ix_messages_wa_message_id'))
        batch_op.drop_index(batch_op.f('ix_messages_wa_conversation_id'))
        batch_op.drop_index(batch_op.f('ix_messages_status'))
        batch_op.drop_index(batch_op.f('ix_messages_lead_id'))
        batch_op.drop_index(batch_op.f('ix_messages_idempotency_key'))
        batch_op.drop_index(batch_op.f('ix_messages_direction'))
        batch_op.drop_index(batch_op.f('ix_messages_counterparty_phone'))

    op.drop_table('messages')
    with op.batch_alter_table('lifecycle_transitions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_lifecycle_transitions_lead_id'))

    op.drop_table('lifecycle_transitions')
    with op.batch_alter_table('leads', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_leads_phone_e164'))
        batch_op.drop_index(batch_op.f('ix_leads_lifecycle_state'))
        batch_op.drop_index(batch_op.f('ix_leads_is_minor'))
        batch_op.drop_index(batch_op.f('ix_leads_human_owned'))
        batch_op.drop_index(batch_op.f('ix_leads_household_id'))
        batch_op.drop_index(batch_op.f('ix_leads_consent_status'))

    op.drop_table('leads')
    with op.batch_alter_table('webhook_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_webhook_events_processed'))
        batch_op.drop_index(batch_op.f('ix_webhook_events_event_hash'))

    op.drop_table('webhook_events')
    with op.batch_alter_table('households', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_households_external_ref'))

    op.drop_table('households')
