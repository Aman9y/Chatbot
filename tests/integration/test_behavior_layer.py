"""The sales-playbook + topic-matrix guidance reaches the LLM prompt each turn."""

from __future__ import annotations

from datetime import timedelta

from app.config import Settings
from app.models.enums import (
    LifecycleState,
    MessageDirection,
    MessageStatus,
    MessageType,
    SentBy,
)
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


async def _run_turn(session, redis, s, wa, kb, text, *, reply_gap_min=2, priors=1):
    now = utcnow()
    lead = Lead(
        phone_e164="+919812345670",
        lifecycle_state=LifecycleState.ENGAGED,
        first_engaged_at=now - timedelta(minutes=30),
        last_outbound_at=now - timedelta(minutes=reply_gap_min),
        last_inbound_at=now,
    )
    session.add(lead)
    await session.flush()
    for i in range(priors):
        session.add(
            Message(
                lead_id=lead.id,
                direction=MessageDirection.INBOUND,
                message_type=MessageType.TEXT,
                body=f"earlier {i}",
                status=MessageStatus.RECEIVED,
                sent_by=SentBy.LEAD,
                wa_message_id=f"wamid.PR{i}",
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

    llm = FakeLLMClient(reply="Got it — shall I set up a quick call with Dr. Rao?")
    engine = ConversationEngine(session, redis, s, llm=llm, kb=kb, wa_client=wa)
    await engine.handle_inbound(lead, msg)
    return llm.calls[-1]["system"], lead


async def test_prompt_carries_pace_topic_and_objection(
    session, redis_client, wa_client, knowledge_base
):
    s = _settings()
    prompt, _ = await _run_turn(
        session,
        redis_client,
        s,
        wa_client,
        knowledge_base,
        "my friend's consultant is cheaper, and can I get a loan for the fees?",
        reply_gap_min=1,
    )
    assert "## Pace & CTA" in prompt
    assert "chat_speed: constant" in prompt
    assert "## Topic handling" in prompt
    assert "HARD DEFLECT" in prompt
    # loan is a hard-deflect topic and must be surfaced (top or "ALSO in this message")
    assert "financing" in prompt.lower() or "loan/emi" in prompt.lower()
    assert "## Objection detected" in prompt
    assert "cheaper_elsewhere" in prompt


async def test_slow_lead_gets_slow_pace_and_nurture_cta(
    session, redis_client, wa_client, knowledge_base
):
    s = _settings(engagement_handoff_hours=48)
    prompt, _ = await _run_turn(
        session,
        redis_client,
        s,
        wa_client,
        knowledge_base,
        "still thinking about which country",
        reply_gap_min=600,
    )
    assert "chat_speed: slow" in prompt


async def test_high_intent_prompt_flag(
    session, redis_client, wa_client, knowledge_base
):
    s = _settings()
    prompt, _ = await _run_turn(
        session,
        redis_client,
        s,
        wa_client,
        knowledge_base,
        "I'm ready to proceed — how do we start the process now?",
    )
    assert "high_intent: yes" in prompt
