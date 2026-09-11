from __future__ import annotations

from sqlalchemy import func, select

from app.config import Settings
from app.models.conversation_trace import ConversationTrace
from app.models.enums import (
    ConsentGate,
    LifecycleState,
    MessageDirection,
    MessageStatus,
    MessageType,
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
        guard_regenerate_attempts=1,
        booking_detection_enabled=True,
        company_name="MedPath",
        counselor_name="Dr. Rao",
    )
    base.update(over)
    return Settings(**base)


async def _engaged_lead(session, redis, settings, *, phone="+919812345670", text="Hi, tell me about Georgia") -> tuple[Lead, Message]:
    lead = Lead(
        phone_e164=phone,
        full_name="Priya",
        lifecycle_state=LifecycleState.ENGAGED,
        first_engaged_at=utcnow(),
        consent_gate=ConsentGate.CLEARED,
    )
    session.add(lead)
    await session.flush()
    msg = Message(
        lead_id=lead.id,
        direction=MessageDirection.INBOUND,
        message_type=MessageType.TEXT,
        body=text,
        status=MessageStatus.RECEIVED,
        sent_by=SentBy.LEAD,
        wa_message_id=f"wamid.IN-{phone[-4:]}",
        status_history=[],
    )
    session.add(msg)
    await session.flush()
    await WindowService(redis, settings).touch(lead)
    await session.commit()
    return lead, msg


def _engine(session, redis, settings, wa_client, knowledge_base, llm):
    return ConversationEngine(
        session, redis, settings, llm=llm, kb=knowledge_base, wa_client=wa_client
    )


async def test_clean_reply_is_sent_and_traced(session, redis_client, wa_client, knowledge_base):
    s = _settings()
    llm = FakeLLMClient(reply="Georgia is a solid option! What draws you to it?")
    lead, msg = await _engaged_lead(session, redis_client, s)

    engine = _engine(session, redis_client, s, wa_client, knowledge_base, llm)
    result = await engine.handle_inbound(lead, msg)

    assert result.action == "sent"
    assert wa_client.sent and wa_client.sent[-1]["kind"] == "text"
    trace = await session.scalar(select(ConversationTrace).where(ConversationTrace.lead_id == lead.id))
    assert trace.final_action == "sent"
    assert trace.guard_verdict == "allowed"
    assert trace.draft_attempts == 1
    assert trace.kb_chunk_ids
    await session.refresh(lead)
    assert lead.lifecycle_state == LifecycleState.ENGAGED


async def test_blocked_draft_is_regenerated(session, redis_client, wa_client, knowledge_base):
    s = _settings()
    llm = FakeLLMClient(
        replies=[
            "Germany comes to about 40 lakh all in.",  # blocked: premium cost
            "Totally understand — our counsellor can give you exact numbers. Call tomorrow?",
        ]
    )
    lead, msg = await _engaged_lead(session, redis_client, s, text="what does Germany cost?")
    engine = _engine(session, redis_client, s, wa_client, knowledge_base, llm)

    result = await engine.handle_inbound(lead, msg)
    assert result.action == "sent"
    assert "40 lakh" not in (result.reply_text or "")
    trace = await session.scalar(select(ConversationTrace).where(ConversationTrace.lead_id == lead.id))
    assert trace.draft_attempts == 2
    assert trace.guard_verdict == "allowed_after_regen"


async def test_persistently_blocked_uses_fallback_and_notifies(
    session, redis_client, wa_client, knowledge_base
):
    s = _settings()
    llm = FakeLLMClient(
        replies=[
            "Germany is about 40 lakh.",
            "Admission is guaranteed if you pay the deposit now.",
            "another bad one with a loan option",
        ]
    )
    lead, msg = await _engaged_lead(session, redis_client, s, text="germany cost and is admission sure?")
    engine = _engine(session, redis_client, s, wa_client, knowledge_base, llm)

    result = await engine.handle_inbound(lead, msg)
    assert result.action == "fallback_sent"
    assert "Dr. Rao" in result.reply_text
    trace = await session.scalar(select(ConversationTrace).where(ConversationTrace.lead_id == lead.id))
    assert trace.guard_verdict == "blocked_fallback"
    note = await session.scalar(
        select(HandoffNotification).where(HandoffNotification.lead_id == lead.id)
    )
    assert note is not None
    assert note.trigger.value == "guard_fallback"


async def test_window_closed_skips(session, redis_client, wa_client, knowledge_base):
    s = _settings()
    llm = FakeLLMClient()
    lead, msg = await _engaged_lead(session, redis_client, s)
    await WindowService(redis_client, s).close(lead)
    await session.commit()

    engine = _engine(session, redis_client, s, wa_client, knowledge_base, llm)
    result = await engine.handle_inbound(lead, msg)
    assert result.action == "skipped"
    assert result.skipped_reason == "window_closed"
    assert not wa_client.sent


async def test_human_owned_skips(session, redis_client, wa_client, knowledge_base):
    s = _settings()
    lead, msg = await _engaged_lead(session, redis_client, s)
    lead.human_owned = True
    await session.commit()
    engine = _engine(session, redis_client, s, wa_client, knowledge_base, FakeLLMClient())
    result = await engine.handle_inbound(lead, msg)
    assert result.skipped_reason.startswith("not_bot_owned")


async def test_autoreply_disabled_skips(session, redis_client, wa_client, knowledge_base):
    s = _settings(bot_autoreply_enabled=False)
    lead, msg = await _engaged_lead(session, redis_client, s)
    engine = _engine(session, redis_client, s, wa_client, knowledge_base, FakeLLMClient())
    result = await engine.handle_inbound(lead, msg)
    assert result.skipped_reason == "autoreply_disabled"


async def test_booking_detected_triggers_handoff(session, redis_client, wa_client, knowledge_base):
    s = _settings()
    llm = FakeLLMClient(reply="Perfect, I'll have Dr. Rao call you then.")
    lead, msg = await _engaged_lead(
        session, redis_client, s, text="ok call me tomorrow evening please"
    )
    engine = _engine(session, redis_client, s, wa_client, knowledge_base, llm)

    result = await engine.handle_inbound(lead, msg)
    assert result.action == "sent"
    assert result.booking_detected
    await session.refresh(lead)
    assert lead.lifecycle_state == LifecycleState.HANDOFF
    assert lead.human_owned
    note = await session.scalar(
        select(HandoffNotification).where(
            HandoffNotification.lead_id == lead.id,
            HandoffNotification.trigger == "booking",
        )
    )
    assert note is not None


async def test_engine_error_is_isolated(session, redis_client, wa_client, knowledge_base):
    s = _settings()
    lead, msg = await _engaged_lead(session, redis_client, s)
    engine = _engine(session, redis_client, s, wa_client, knowledge_base, FakeLLMClient(raise_error=True))

    result = await engine.handle_inbound(lead, msg)
    assert result.action == "error"
    trace = await session.scalar(
        select(ConversationTrace).where(ConversationTrace.final_action == "error")
    )
    assert trace is not None and trace.error
    assert await session.scalar(select(func.count()).select_from(Message).where(Message.direction == MessageDirection.OUTBOUND)) == 0
