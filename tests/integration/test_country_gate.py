"""Director review: the "country discussed" gate before any Rafique Sir CTA.

This is enforced STATE, not a prompt instruction — Lead.country_discussed is
real, persistent lead state recomputed every turn from actual message history
(app/services/conversation/context.py), and the Response Guard refuses to let
a draft mention a call / the director's number / the office while it is still
false (app/services/guard/guard.py:_check_premature_contact_offer), exactly
like an out-of-range cost figure. These tests prove the block happens even
when the LLM ignores the prompt hint entirely — the point of enforcing state
instead of relying on wording.
"""

from __future__ import annotations

from app.config import Settings
from app.models.enums import (
    ConsentGate,
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
        guard_regenerate_attempts=1,
        booking_detection_enabled=False,
        counselor_name="Rafique Shaikh",
        counselor_phone="+91 74478 67887",
    )
    base.update(over)
    return Settings(**base)


async def _lead(session, redis, s, phone="+919812345670") -> Lead:
    lead = Lead(
        phone_e164=phone,
        lifecycle_state=LifecycleState.ENGAGED,
        first_engaged_at=utcnow(),
        consent_gate=ConsentGate.CLEARED,
    )
    session.add(lead)
    await session.flush()
    await WindowService(redis, s).touch(lead)
    await session.commit()
    return lead


async def _inbound(session, lead, text, wamid) -> Message:
    msg = Message(
        lead_id=lead.id,
        direction=MessageDirection.INBOUND,
        message_type=MessageType.TEXT,
        body=text,
        status=MessageStatus.RECEIVED,
        sent_by=SentBy.LEAD,
        wa_message_id=wamid,
        status_history=[],
    )
    session.add(msg)
    await session.commit()
    return msg


async def test_premature_cta_is_blocked_even_when_the_model_ignores_the_hint(
    session, redis_client, wa_client, knowledge_base
):
    """The LLM tries to pitch the call on message one regardless of the
    per-turn prompt hint — proves the block is a hard guard rule, not
    something that only works if the model reads the hint."""

    s = _settings()
    llm = FakeLLMClient(
        reply="Georgia's a solid pick! Shall I set up a call with Rafique Sir, "
        "or share his number?"
    )
    lead = await _lead(session, redis_client, s)
    msg = await _inbound(session, lead, "Hi, tell me about Georgia", "wamid.G1")

    engine = ConversationEngine(session, redis_client, s, llm=llm, kb=knowledge_base, wa_client=wa_client)
    result = await engine.handle_inbound(lead, msg)

    assert result.action == "fallback_sent"
    reply = (result.reply_text or "").lower()
    assert "rafique" not in reply
    assert "74478" not in reply
    assert "call" not in reply or "which one are you considering" in reply

    await session.refresh(lead)
    assert lead.country_discussed is False


async def test_gate_unlocks_after_a_real_country_back_and_forth(
    session, redis_client, wa_client, knowledge_base
):
    """Turn 1: lead names a country, bot (correctly) only discusses it — no
    CTA yet, gate still closed. Turn 2: bot's own reply names the country too
    — the back-and-forth is now real on both sides, so the gate opens. Turn 3:
    the bot may now offer the call and the guard allows it."""

    s = _settings()

    def responder(system, messages, purpose):
        if "GATE — no call, number, or office yet" in system:
            return "Georgia has NMC-recognised government medical universities."
        return "Great — Rafique Sir can talk you through it. Shall I set up a call?"

    llm = FakeLLMClient(responder=responder)
    lead = await _lead(session, redis_client, s)
    engine = ConversationEngine(session, redis_client, s, llm=llm, kb=knowledge_base, wa_client=wa_client)

    # turn 1: lead names Georgia; bot has never named a country before -> gated
    msg1 = await _inbound(session, lead, "Hi, tell me about Georgia", "wamid.G1")
    r1 = await engine.handle_inbound(lead, msg1)
    assert r1.action == "sent"
    await session.refresh(lead)
    assert lead.country_discussed is False

    # turn 2: bot's reply above named Georgia, so history now has BOTH sides
    # naming a country -> the gate opens for the NEXT turn.
    msg2 = await _inbound(session, lead, "sounds good, what else should I know?", "wamid.G2")
    r2 = await engine.handle_inbound(lead, msg2)
    assert r2.action == "sent"
    await session.refresh(lead)
    assert lead.country_discussed is True

    # turn 3: the gate is open, so a reply offering the call is now allowed.
    msg3 = await _inbound(session, lead, "ok what next", "wamid.G3")
    r3 = await engine.handle_inbound(lead, msg3)
    assert r3.action == "sent"
    assert "rafique" in (r3.reply_text or "").lower()


async def test_below_cutoff_leads_are_exempt_from_the_country_gate(
    session, redis_client, wa_client, knowledge_base
):
    """A below-cutoff lead has no country left to pick — their path is the
    private-India / re-attempt conversation, so the call is offered honestly
    on message one without needing a country discussion first."""

    s = _settings(neet_cutoff_general=213, neet_cutoff_obc=175)
    llm = FakeLLMClient(
        reply="With that score the abroad route is closed this cycle, but "
        "private MBBS in India can still work. Shall I set up a call with "
        "Rafique Sir to talk through it?"
    )
    lead = await _lead(session, redis_client, s)
    msg = await _inbound(session, lead, "my NEET was 120, general, what can I do?", "wamid.B1")

    engine = ConversationEngine(session, redis_client, s, llm=llm, kb=knowledge_base, wa_client=wa_client)
    result = await engine.handle_inbound(lead, msg)

    assert result.action == "sent"
    assert "rafique" in (result.reply_text or "").lower()
