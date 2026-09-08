"""End-to-end: signed webhook POST -> ingestion -> consent+age gate -> auto-reply.

Inbound-first leads skip the opt-in ask (they messaged us) and land at the age
check; the sales flow only runs once they confirm 18+.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.conversation_trace import ConversationTrace
from app.models.enums import ConsentGate, MessageDirection
from app.models.lead import Lead
from app.models.message import Message
from tests.helpers import as_bytes, inbound_text_payload, signed_headers


async def _post(client, payload):
    raw = as_bytes(payload)
    return await client.post("/webhook/whatsapp", content=raw, headers=signed_headers(raw))


async def _traces(session, lead_id):
    return (
        await session.scalars(
            select(ConversationTrace)
            .where(ConversationTrace.lead_id == lead_id)
            .order_by(ConversationTrace.created_at)
        )
    ).all()


async def test_gate_then_sales_reply(conversation_api_client, session):
    # 1. first inbound -> age check (opt-in implied by messaging us)
    resp = await _post(
        conversation_api_client,
        inbound_text_payload(wamid="wamid.G1", from_="919812345670", text="Hi, is MBBS in Georgia good?"),
    )
    assert resp.status_code == 200
    lead = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345670"))
    assert lead.consent_gate == ConsentGate.PENDING_AGE
    t1 = await _traces(session, lead.id)
    assert t1[-1].final_action == "gate_age_ask"
    assert "18" in (t1[-1].final_text or "")

    # 2. confirms adult -> gate clears, sales flow runs on the same turn
    await _post(
        conversation_api_client,
        inbound_text_payload(wamid="wamid.G2", from_="919812345670", text="yeah I'm 24"),
    )
    await session.refresh(lead)
    assert lead.consent_gate == ConsentGate.CLEARED
    assert lead.consent_verified is True
    t2 = await _traces(session, lead.id)
    assert t2[-1].final_action == "sent"

    msgs = (
        await session.scalars(
            select(Message).where(Message.lead_id == lead.id).order_by(Message.created_at)
        )
    ).all()
    dirs = [m.direction for m in msgs]
    assert dirs.count(MessageDirection.OUTBOUND) == 2  # age ask + sales reply
    assert conversation_api_client.app.state.wa_client.sent


async def test_gate_decline_opts_out(conversation_api_client, session):
    await _post(
        conversation_api_client,
        inbound_text_payload(wamid="wamid.D1", from_="919812345690", text="hi"),
    )
    await _post(
        conversation_api_client,
        inbound_text_payload(wamid="wamid.D2", from_="919812345690", text="no thanks, not interested"),
    )
    lead = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345690"))
    assert lead.consent_gate == ConsentGate.REFUSED
    assert lead.lifecycle_state.value == "opted_out"
    t = await _traces(session, lead.id)
    assert t[-1].final_action == "gate_declined"


async def test_under_18_is_held(conversation_api_client, session):
    from app.models.enums import HandoffTrigger, MinorStatus
    from app.models.handoff import HandoffNotification

    await _post(
        conversation_api_client,
        inbound_text_payload(wamid="wamid.M1", from_="919812345691", text="hello"),
    )
    await _post(
        conversation_api_client,
        inbound_text_payload(wamid="wamid.M2", from_="919812345691", text="I'm 17"),
    )
    lead = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345691"))
    assert lead.consent_gate == ConsentGate.MINOR_HOLD
    assert lead.is_minor == MinorStatus.YES
    assert lead.minor_policy_status.value == "pending_review"
    assert lead.lifecycle_state.value == "gate_hold"
    assert lead.human_owned is True
    note = await session.scalar(
        select(HandoffNotification).where(
            HandoffNotification.lead_id == lead.id,
            HandoffNotification.trigger == HandoffTrigger.MINOR_HOLD,
        )
    )
    assert note is not None
    # a further message gets no bot reply
    await _post(
        conversation_api_client,
        inbound_text_payload(wamid="wamid.M3", from_="919812345691", text="but I really want to apply"),
    )
    t = await _traces(session, lead.id)
    assert t[-1].skipped_reason in ("consent_gate:minor_hold", "not_bot_owned:gate_hold")


async def test_unclear_answer_reasks_then_parks(conversation_api_client, session):
    await _post(
        conversation_api_client,
        inbound_text_payload(wamid="wamid.U1", from_="919812345692", text="hi"),
    )
    # ambiguous age answer -> re-ask
    await _post(
        conversation_api_client,
        inbound_text_payload(wamid="wamid.U2", from_="919812345692", text="why do you need that"),
    )
    lead = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345692"))
    assert lead.gate_reask_count == 1
    t = await _traces(session, lead.id)
    assert t[-1].final_action == "gate_reask"
    # still ambiguous -> parked for a human
    await _post(
        conversation_api_client,
        inbound_text_payload(wamid="wamid.U3", from_="919812345692", text="hmm"),
    )
    await session.refresh(lead)
    assert lead.consent_gate == ConsentGate.NEEDS_HUMAN
    assert lead.lifecycle_state.value == "gate_hold"
    t = await _traces(session, lead.id)
    assert t[-1].final_action == "gate_review"


async def test_opt_out_webhook_gets_no_bot_reply(conversation_api_client, session):
    await _post(
        conversation_api_client,
        inbound_text_payload(wamid="wamid.CONV2", from_="919812345671", text="STOP"),
    )
    lead = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345671"))
    outbound = await session.scalar(
        select(Message).where(
            Message.lead_id == lead.id, Message.direction == MessageDirection.OUTBOUND
        )
    )
    assert outbound is None
    assert not conversation_api_client.app.state.wa_client.sent


async def test_second_inbound_uses_history_after_gate(conversation_api_client, session):
    await _post(
        conversation_api_client,
        inbound_text_payload(wamid="wamid.H0", from_="919812345672", text="Hi"),
    )
    await _post(
        conversation_api_client,
        inbound_text_payload(wamid="wamid.H1", from_="919812345672", text="I'm 20"),
    )
    await _post(
        conversation_api_client,
        inbound_text_payload(wamid="wamid.H2", from_="919812345672", text="what about Russia?"),
    )
    lead = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345672"))
    sales_traces = [
        t for t in await _traces(session, lead.id) if t.final_action == "sent"
    ]
    assert len(sales_traces) == 2
    sent = conversation_api_client.app.state.wa_client.sent
    assert len(sent) >= 3  # age ask + 2 sales replies
