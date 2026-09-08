"""Counsellor handoff notifications.

Phase 3: persist a HandoffNotification row and (if configured) POST it to
counselor_webhook_url. The counsellor CRM integration is a later phase.
"""

from __future__ import annotations

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.logging_config import get_logger, log_extra, mask_phone
from app.models.enums import HandoffTrigger
from app.models.handoff import HandoffNotification
from app.models.lead import Lead
from app.services.timeutils import utcnow

logger = get_logger(__name__)


async def notify_counselor(
    session: AsyncSession,
    settings: Settings,
    lead: Lead,
    *,
    trigger: HandoffTrigger,
    summary: str,
    context: dict | None = None,
    dedupe: bool = True,
) -> HandoffNotification | None:
    """Create a handoff notification. With `dedupe`, an undelivered notification
    with the same trigger for this lead is reused instead of duplicated."""

    if dedupe:
        existing = await session.scalar(
            select(HandoffNotification)
            .where(HandoffNotification.lead_id == lead.id)
            .where(HandoffNotification.trigger == trigger)
            .where(HandoffNotification.acknowledged_at.is_(None))
        )
        if existing is not None:
            return existing

    note = HandoffNotification(
        lead_id=lead.id,
        trigger=trigger,
        summary=summary[:2000],
        context=context or {},
    )
    session.add(note)
    await session.flush()

    logger.info(
        "counsellor handoff queued",
        extra=log_extra(
            lead=mask_phone(lead.phone_e164),
            trigger=trigger.value,
            summary=summary[:200],
        ),
    )

    if settings.counselor_webhook_url.strip():
        await _deliver_webhook(session, settings, lead, note)
    else:
        note.delivery_channel = "log"
        note.delivered = True
        note.delivered_at = utcnow()

    return note


async def _deliver_webhook(
    session: AsyncSession,
    settings: Settings,
    lead: Lead,
    note: HandoffNotification,
) -> None:
    payload = {
        "lead_id": str(lead.id),
        "phone": lead.phone_e164,
        "name": lead.full_name,
        "trigger": note.trigger.value,
        "summary": note.summary,
        "lifecycle_state": lead.lifecycle_state.value,
        "assigned_counselor": lead.assigned_counselor,
        "context": note.context,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            resp = await client.post(settings.counselor_webhook_url, json=payload)
            resp.raise_for_status()
        note.delivered = True
        note.delivered_at = utcnow()
        note.delivery_channel = "webhook"
    except httpx.HTTPError as exc:  # pragma: no cover - network
        note.delivery_channel = "webhook"
        note.delivery_error = str(exc)[:1000]
        logger.error("counsellor webhook delivery failed: %s", exc)
