"""Engine wiring for in-conversation qualification + lead scoring."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.config import Settings
from app.models.conversation_trace import ConversationTrace
from app.models.enums import (
    ConsentGate,
    EligibilityFlag,
    HandoffTrigger,
    LeadScore,
    LifecycleState,
    MessageDirection,
    MessageStatus,
    MessageType,
    NeetCategory,
    SentBy,
)
from app.models.handoff import HandoffNotification
from app.models.lead import Lead
from app.models.message import Message
from app.services.conversation.engine import ConversationEngine
from app.services.llm.fake import FakeLLMClient
from app.services.timeutils import utcnow
from app.services.windows import WindowService


def _settings(**over) -> Settings:
    base = dict(
        llm_provider="fake",
        bot_autoreply_enabled=True,
        guard_enabled=True,
        booking_detection_enabled=False,
        counselor_name="Dr. Rao",
    )
    base.update(over)
    return Settings(**base)


async def _lead_with_inbound(session, redis, s, text, *, fast=True) -> tuple[Lead, Message]:
    now = utcnow()
    lead = Lead(
        phone_e164="+919812345670",
        lifecycle_state=LifecycleState.ENGAGED,
        first_engaged_at=now - timedelta(minutes=20),
        last_outbound_at=now - timedelta(minutes=2 if fast else 500),
        last_inbound_at=now,
        consent_gate=ConsentGate.CLEARED,
    )
    session.add(lead)
    await session.flush()
    for i, prior in enumerate(["hi", "tell me more"]):
        session.add(
            Message(
                lead_id=lead.id,
                direction=MessageDirection.INBOUND,
                message_type=MessageType.TEXT,
                body=prior,
                status=MessageStatus.RECEIVED,
                sent_by=SentBy.LEAD,
                wa_message_id=f"wamid.P{i}",
                status_history=[],
            )
        )
    msg = Message(
        lead_id=lead.id,
        direction=MessageDirection.INBOUND,
        message_type=MessageType.TEXT,
        body=text,
        status=MessageStatus.RECEIVED,
        sent_by=SentBy.LEAD,
        wa_message_id="wamid.NOW",
        status_history=[],
    )
    session.add(msg)
    await session.flush()
    await WindowService(redis, s).touch(lead)
    await session.commit()
    return lead, msg


async def test_engine_extracts_qualifiers_and_scores(
    session, redis_client, wa_client, knowledge_base
):
    s = _settings()
    llm = FakeLLMClient(reply="Great, Georgia's a solid pick! Shall I set up a call?")
    lead, msg = await _lead_with_inbound(
        session,
        redis_client,
        s,
        "I got 250 in NEET, general category, 60% in PCB, want Georgia, starting this year",
    )
    engine = ConversationEngine(
        session, redis_client, s, llm=llm, kb=knowledge_base, wa_client=wa_client
    )
    result = await engine.handle_inbound(lead, msg)
    assert result.action == "sent"

    await session.refresh(lead)
    assert lead.neet_score == 250
    assert lead.neet_category == NeetCategory.GENERAL
    assert lead.target_country == "Georgia"
    assert lead.eligibility_flag == EligibilityFlag.ABOVE_CUTOFF
    assert lead.urgency.value == "this_intake"
    assert lead.lead_score == LeadScore.HIGH
    assert lead.lead_score_reason
    assert lead.counsellor_cta_sent is True

    trace = await session.scalar(
        select(ConversationTrace).where(ConversationTrace.lead_id == lead.id)
    )
    assert trace.extracted_qualifiers
    assert trace.lead_score == "high"

    note = await session.scalar(
        select(HandoffNotification).where(
            HandoffNotification.lead_id == lead.id,
            HandoffNotification.trigger == HandoffTrigger.HIGH_INTENT,
        )
    )
    assert note is not None
    # a second turn must NOT re-notify
    msg2 = Message(
        lead_id=lead.id,
        direction=MessageDirection.INBOUND,
        message_type=MessageType.TEXT,
        body="and is it safe there?",
        status=MessageStatus.RECEIVED,
        sent_by=SentBy.LEAD,
        wa_message_id="wamid.NOW2",
        status_history=[],
    )
    session.add(msg2)
    await session.commit()
    await engine.handle_inbound(lead, msg2)
    notes = (
        await session.scalars(
            select(HandoffNotification).where(
                HandoffNotification.lead_id == lead.id,
                HandoffNotification.trigger == HandoffTrigger.HIGH_INTENT,
            )
        )
    ).all()
    assert len(notes) == 1


async def test_below_cutoff_lead_scores_low_no_cta(
    session, redis_client, wa_client, knowledge_base
):
    s = _settings()
    llm = FakeLLMClient(
        reply="I'll be honest — that score closes the abroad route this cycle. "
        "Private MBBS in India can still work. Want a call with Dr. Rao?"
    )
    lead, msg = await _lead_with_inbound(
        session, redis_client, s, "my NEET was 150, general, what can I do?"
    )
    engine = ConversationEngine(
        session, redis_client, s, llm=llm, kb=knowledge_base, wa_client=wa_client
    )
    result = await engine.handle_inbound(lead, msg)
    assert result.action == "sent"
    await session.refresh(lead)
    assert lead.eligibility_flag == EligibilityFlag.BELOW_CUTOFF
    assert lead.lead_score == LeadScore.LOW
    assert lead.counsellor_cta_sent is False
    note = await session.scalar(
        select(HandoffNotification).where(
            HandoffNotification.lead_id == lead.id,
            HandoffNotification.trigger == HandoffTrigger.HIGH_INTENT,
        )
    )
    assert note is None
