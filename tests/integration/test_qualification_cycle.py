"""Director review: the qualification cycle (interest -> eligibility ->
country -> Rafique Sir) as an always-return-to spine, verified end to end
through the real ConversationEngine.

Each test drives a FakeLLMClient with a `responder` that reads the per-turn
"CYCLE STEP" markers the engine injects (app/services/conversation/context.py)
and answers accordingly — proving the tracked state (not message count, not
prompt-only wording) actually drives the conversation through the cycle, that
off-script questions get answered without derailing the pending step, that
answered steps are never re-asked, and that Rafique Sir never appears before
country_discussed is genuinely true.
"""

from __future__ import annotations

import re

from app.config import Settings
from app.models.enums import (
    ConsentGate,
    EligibilityFlag,
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


def _cycle_responder(system: str, messages, purpose: str) -> str:
    """Always answer whatever real question is on the table AND, if a CYCLE
    STEP is active, weave in that step's ask/content — mirroring exactly what
    the per-turn instructions ask a compliant model to do."""

    last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
    asked_nmc = "nmc" in last_user.lower() or "recognition" in last_user.lower()
    nmc_answer = (
        "Good question — the university has to meet all six NMC criteria "
        "(English medium, 5+1 structure, no mid-course transfer, WHO-listed, "
        "own teaching hospital, graduates can practise locally). "
        if asked_nmc
        else ""
    )

    if "CYCLE STEP — interest" in system:
        return nmc_answer + "Are you looking at MBBS abroad, or MBBS in India?"
    if "CYCLE STEP — NEET score" in system:
        return nmc_answer + "Got it — what was your NEET score?"
    if "CYCLE STEP — reservation category" in system:
        return nmc_answer + "Quick one — are you general category or OBC/SC/ST/EWS?"
    if "CYCLE STEP — PCB percentage" in system:
        return nmc_answer + "And what was your PCB percentage — Physics, Chemistry, Biology combined?"
    if "CYCLE STEP — country decision" in system:
        return nmc_answer + "Have you decided on a country, or are you still deciding?"
    if "CYCLE STEP — country: still deciding" in system:
        return (
            "Bangladesh is very close to India but a bit costlier, Georgia has "
            "great social/campus life, Russia is similar to Georgia with "
            "slightly less on the social side, and Uzbekistan, Kazakhstan and "
            "Kyrgyzstan are stable, well-established options worth exploring. "
            "Every location keeps separate facilities for male and female "
            "students, and medical/health and security are fully handled "
            "everywhere — you're the priority. Any of these stand out?"
        )
    if "(decided)" in system and "CYCLE STEP — country" in system:
        # the confirmed college list lives in the KB block further down the
        # prompt; the fake model just needs to name the country + reference
        # the standing "open to suggestions" close.
        m = re.search(r"CYCLE STEP — country: (.+?) \(decided\)", system)
        country = m.group(1) if m else "that country"
        return (
            f"Great pick, {country}! We place students at recognised "
            "government universities there. We're also open to any specific "
            "college you have in mind — happy to look into that too."
        )
    if "introduce Rafique Sir" in system:
        return (
            "Our director, Rafique Sir, has 10+ years of experience in this "
            "field and can guide you better on the exact details — I'm just "
            "Stellar AI, an assistant. Please reach out to him directly for "
            "more specific guidance on +91 74478 67887."
        )
    return nmc_answer + "Happy to help with whatever you need next."


async def test_full_cycle_straight_through(session, redis_client, wa_client, knowledge_base):
    s = _settings()
    llm = FakeLLMClient(responder=_cycle_responder)
    lead = await _lead(session, redis_client, s)
    engine = ConversationEngine(session, redis_client, s, llm=llm, kb=knowledge_base, wa_client=wa_client)

    async def turn(text, wamid):
        msg = await _inbound(session, lead, text, wamid)
        r = await engine.handle_inbound(lead, msg)
        assert r.action == "sent", r.reply_text
        await session.refresh(lead)
        return r

    # 1. answers the fixed opener with "yes" -> interest confirmed
    r1 = await turn("Hi! Yes, I'm interested in MBBS abroad", "wamid.C1")
    assert lead.considering_abroad is True
    assert "rafique" not in (r1.reply_text or "").lower()

    # 2. NEET score
    r2 = await turn("I got 250 in NEET", "wamid.C2")
    assert lead.neet_score == 250
    assert "rafique" not in (r2.reply_text or "").lower()

    # 3. PCB percentage -> eligibility resolves
    r3 = await turn("my PCB percentage was 65%", "wamid.C3")
    assert lead.pcb_percentage == 65.0
    assert lead.eligibility_flag == EligibilityFlag.ABOVE_CUTOFF
    assert "rafique" not in (r3.reply_text or "").lower()

    # 4. still deciding -> exact comparison substance + safety line
    r4 = await turn("still deciding, can you compare a few for me?", "wamid.C4")
    assert lead.country_still_deciding is True
    reply4 = (r4.reply_text or "").lower()
    for needle in ("bangladesh", "georgia", "russia", "uzbekistan", "kazakhstan", "kyrgyzstan"):
        assert needle in reply4
    assert "male and female" in reply4
    assert "rafique" not in reply4  # country_discussed not true yet (lead hasn't named one)

    # 5. lead finally names a country -> real back-and-forth complete
    r5 = await turn("Georgia sounds good actually", "wamid.C5")
    assert lead.target_country == "Georgia"
    assert lead.country_discussed is True
    # Rafique Sir now appears, with the exact first-time framing
    reply5 = (r5.reply_text or "").lower()
    assert "rafique sir" in reply5
    assert "10+ years" in reply5 or "10 years" in reply5 or "10+" in reply5
    assert "i'm just stellar ai" in reply5

    # 6. cycle is complete — the bot keeps managing the chat, doesn't go quiet,
    #    and doesn't force the intro framing again every turn.
    r6 = await turn("ok thanks", "wamid.C6")
    assert r6.action == "sent"


async def test_off_script_question_answered_without_dropping_the_pending_step(
    session, redis_client, wa_client, knowledge_base
):
    s = _settings()
    llm = FakeLLMClient(responder=_cycle_responder)
    lead = await _lead(session, redis_client, s)
    engine = ConversationEngine(session, redis_client, s, llm=llm, kb=knowledge_base, wa_client=wa_client)

    async def turn(text, wamid):
        msg = await _inbound(session, lead, text, wamid)
        r = await engine.handle_inbound(lead, msg)
        assert r.action == "sent", r.reply_text
        await session.refresh(lead)
        return r

    await turn("Yes, interested in abroad", "wamid.D1")
    r2 = await turn("I got 250 in NEET. Also, is NMC recognition a big deal?", "wamid.D2")
    reply2 = (r2.reply_text or "").lower()
    # (a) the off-script NMC question gets a real answer
    assert "nmc" in reply2 or "recognised" in reply2 or "criteria" in reply2
    # (b) PCB still gets asked afterward — not skipped or forgotten
    assert "pcb" in reply2

    r3 = await turn("PCB was 60%", "wamid.D3")
    assert lead.eligibility_flag == EligibilityFlag.ABOVE_CUTOFF
    # country decision step now active, not re-asking anything already answered
    reply3 = (r3.reply_text or "").lower()
    assert "neet score" not in reply3
    assert "country" in reply3


async def test_naming_a_country_immediately_skips_still_deciding(
    session, redis_client, wa_client, knowledge_base
):
    s = _settings()
    llm = FakeLLMClient(responder=_cycle_responder)
    lead = await _lead(session, redis_client, s)
    engine = ConversationEngine(session, redis_client, s, llm=llm, kb=knowledge_base, wa_client=wa_client)

    async def turn(text, wamid):
        msg = await _inbound(session, lead, text, wamid)
        r = await engine.handle_inbound(lead, msg)
        assert r.action == "sent", r.reply_text
        await session.refresh(lead)
        return r

    await turn("Yes, abroad", "wamid.E1")
    await turn("250 in NEET", "wamid.E2")
    r3 = await turn("PCB 70%", "wamid.E3")
    assert lead.eligibility_flag == EligibilityFlag.ABOVE_CUTOFF
    assert "rafique" not in (r3.reply_text or "").lower()

    # skip "still deciding" entirely — name a country the moment eligible
    r4 = await turn("I want Georgia", "wamid.E4")
    assert lead.target_country == "Georgia"
    reply4 = (r4.reply_text or "").lower()
    assert "any specific college you have in mind" in reply4
    assert "rafique" not in reply4  # bot hasn't named a country in a PRIOR reply yet
    assert lead.country_still_deciding is False

    # the lead's own reply now completes the back-and-forth (bot named
    # Georgia this turn, lead named it too) — Rafique unlocks next turn.
    r5 = await turn("sounds good", "wamid.E5")
    assert lead.country_discussed is True
    assert "rafique sir" in (r5.reply_text or "").lower()
