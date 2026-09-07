"""Inbound / outbound message persistence + out-of-order status reconciliation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import OptOutViolation
from app.models.enums import (
    STATUS_RANK,
    ConsentStatus,
    LifecycleState,
    MessageDirection,
    MessageStatus,
    MessageType,
    SentBy,
    TemplateCategory,
)
from app.models.lead import Lead
from app.models.message import Message
from app.services import leads as leads_service
from app.services.phone import NormalizedPhone, normalize_wa_id
from app.services.pricing import estimate_cost
from app.services.timeutils import utcnow


def _hist(status: str, at: datetime | None, source: str) -> dict[str, Any]:
    return {"status": status, "at": (at or utcnow()).isoformat(), "source": source}


async def get_by_wamid(session: AsyncSession, wamid: str) -> Message | None:
    return await session.scalar(select(Message).where(Message.wa_message_id == wamid))


async def get_outbound_by_idempotency_key(
    session: AsyncSession, key: str
) -> Message | None:
    return await session.scalar(select(Message).where(Message.idempotency_key == key))


async def persist_inbound(
    session: AsyncSession,
    lead: Lead,
    *,
    wa_message_id: str | None,
    message_type: MessageType,
    body: str | None,
    from_phone: str | None,
    to_phone: str | None,
    wa_timestamp: datetime | None,
    raw_payload: dict[str, Any] | None,
    webhook_event_id: UUID | None,
    conversation_id: str | None = None,
) -> tuple[Message, bool]:
    """Returns (message, created). Idempotent on wa_message_id."""

    if wa_message_id:
        existing = await get_by_wamid(session, wa_message_id)
        if existing is not None:
            return existing, False

    when = wa_timestamp or utcnow()
    message = Message(
        lead_id=lead.id,
        direction=MessageDirection.INBOUND,
        wa_message_id=wa_message_id,
        wa_conversation_id=conversation_id,
        counterparty_phone=from_phone,
        from_phone=from_phone,
        to_phone=to_phone,
        message_type=message_type,
        body=body,
        status=MessageStatus.RECEIVED,
        status_updated_at=when,
        status_history=[_hist(MessageStatus.RECEIVED.value, when, "inbound")],
        sent_by=SentBy.LEAD,
        wa_timestamp=when,
        raw_payload=raw_payload,
        webhook_event_id=webhook_event_id,
    )
    session.add(message)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        session.expunge(message)
        existing = await get_by_wamid(session, wa_message_id) if wa_message_id else None
        if existing is None:  # pragma: no cover
            raise
        return existing, False

    if lead.last_inbound_at is None or when > lead.last_inbound_at:
        lead.last_inbound_at = when
    return message, True


async def persist_outbound(
    session: AsyncSession,
    lead: Lead,
    *,
    message_type: MessageType,
    body: str | None = None,
    template_name: str | None = None,
    template_language: str | None = None,
    template_category: TemplateCategory | None = None,
    template_variables: dict[str, Any] | None = None,
    to_phone: str | None = None,
    wa_message_id: str | None = None,
    wa_conversation_id: str | None = None,
    status: MessageStatus = MessageStatus.SENT,
    sent_by: SentBy = SentBy.BOT,
    wa_timestamp: datetime | None = None,
    raw_payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> Message:
    """Persist an outbound message. Hard-refuses if the lead is opted out
    (belt-and-braces alongside the outreach guard — critique 'opted-out leads
    can never be contacted')."""

    if (
        lead.consent_status == ConsentStatus.OPTED_OUT
        or lead.lifecycle_state == LifecycleState.OPTED_OUT
    ):
        raise OptOutViolation(lead.id)

    if idempotency_key:
        existing = await session.scalar(
            select(Message).where(Message.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return existing

    when = wa_timestamp or utcnow()
    message = Message(
        lead_id=lead.id,
        direction=MessageDirection.OUTBOUND,
        wa_message_id=wa_message_id,
        wa_conversation_id=wa_conversation_id,
        counterparty_phone=to_phone or lead.phone_e164,
        from_phone=None,
        to_phone=to_phone or lead.phone_e164,
        message_type=message_type,
        body=body,
        template_name=template_name,
        template_language=template_language,
        template_category=template_category,
        template_variables=template_variables,
        status=status,
        status_updated_at=when,
        status_history=[_hist(status.value, when, "outbound")],
        sent_by=sent_by,
        wa_timestamp=when,
        raw_payload=raw_payload,
        idempotency_key=idempotency_key,
    )
    session.add(message)
    await session.flush()

    if lead.last_outbound_at is None or when > lead.last_outbound_at:
        lead.last_outbound_at = when
    return message


async def apply_status_update(
    session: AsyncSession,
    settings: Settings,
    *,
    wa_message_id: str,
    status: MessageStatus,
    wa_timestamp: datetime | None,
    recipient_wa_id: str,
    conversation_id: str | None,
    pricing: dict[str, Any] | None,
    errors: list[dict[str, Any]] | None,
    raw_payload: dict[str, Any] | None,
    webhook_event_id: UUID | None,
) -> Message:
    """Apply a status webhook. Handles the out-of-order case where the status
    arrives before we have recorded the outbound message (critique C4)."""

    message = await get_by_wamid(session, wa_message_id)
    when = wa_timestamp or utcnow()

    if message is None:
        recipient: NormalizedPhone = normalize_wa_id(
            recipient_wa_id, settings.default_phone_region
        )
        lead, _ = await leads_service.find_or_create(
            session, recipient, source="whatsapp_status"
        )
        message = Message(
            lead_id=lead.id,
            direction=MessageDirection.OUTBOUND,
            wa_message_id=wa_message_id,
            wa_conversation_id=conversation_id,
            counterparty_phone=recipient.e164,
            to_phone=recipient.e164,
            message_type=MessageType.UNKNOWN,
            status=MessageStatus.QUEUED,
            status_history=[],
            sent_by=SentBy.SYSTEM,
            raw_payload={"reconstructed_from": "status_webhook", "payload": raw_payload},
            webhook_event_id=webhook_event_id,
        )
        session.add(message)
        try:
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:  # pragma: no cover - concurrent create
            session.expunge(message)
            message = await get_by_wamid(session, wa_message_id)
            assert message is not None

    message.status_history = [
        *message.status_history,
        _hist(status.value, when, "status_webhook"),
    ]

    if STATUS_RANK.get(status, 0) > STATUS_RANK.get(message.status, 0):
        message.status = status
        message.status_updated_at = when

    if conversation_id and not message.wa_conversation_id:
        message.wa_conversation_id = conversation_id

    if status == MessageStatus.FAILED and errors:
        err = errors[0]
        message.error_code = str(err.get("code")) if err.get("code") is not None else None
        message.error_title = err.get("title") or err.get("message")
        detail = err.get("error_data")
        if isinstance(detail, dict):
            message.error_detail = detail.get("details")
        message.error_payload = err

    if pricing:
        message.pricing_model = pricing.get("pricing_model")
        message.pricing_category = pricing.get("category")
        message.pricing_type = pricing.get("type")
        message.billable = pricing.get("billable")
        amount, currency = estimate_cost(
            settings,
            category=pricing.get("category"),
            country_iso2=None,
            billable=pricing.get("billable"),
        )
        if amount is not None:
            message.cost_amount = Decimal(amount)
            message.cost_currency = currency

    await session.flush()
    return message
