"""End-to-end: signed webhook POST -> ingestion -> conversation engine auto-reply."""

from __future__ import annotations

from sqlalchemy import select

from app.models.conversation_trace import ConversationTrace
from app.models.enums import MessageDirection
from app.models.lead import Lead
from app.models.message import Message
from tests.helpers import as_bytes, inbound_text_payload, signed_headers


async def _post(client, payload):
    raw = as_bytes(payload)
    return await client.post("/webhook/whatsapp", content=raw, headers=signed_headers(raw))


async def test_inbound_webhook_produces_bot_reply(conversation_api_client, session):
    payload = inbound_text_payload(
        wamid="wamid.CONV1", from_="919812345670", text="Hi, is MBBS in Georgia good?"
    )
    resp = await _post(conversation_api_client, payload)
    assert resp.status_code == 200

    lead = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345670"))
    msgs = (
        await session.scalars(
            select(Message).where(Message.lead_id == lead.id).order_by(Message.created_at)
        )
    ).all()
    assert [m.direction for m in msgs] == [
        MessageDirection.INBOUND,
        MessageDirection.OUTBOUND,
    ]
    assert msgs[1].body  # the bot's reply
    assert conversation_api_client.app.state.wa_client.sent

    trace = await session.scalar(
        select(ConversationTrace).where(ConversationTrace.lead_id == lead.id)
    )
    assert trace.final_action == "sent"


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


async def test_second_inbound_uses_history(conversation_api_client, session):
    await _post(
        conversation_api_client,
        inbound_text_payload(wamid="wamid.CONV3a", from_="919812345672", text="Hi"),
    )
    await _post(
        conversation_api_client,
        inbound_text_payload(wamid="wamid.CONV3b", from_="919812345672", text="what about Russia?"),
    )
    lead = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345672"))
    traces = (
        await session.scalars(
            select(ConversationTrace)
            .where(ConversationTrace.lead_id == lead.id)
            .order_by(ConversationTrace.created_at)
        )
    ).all()
    assert len(traces) == 2
    # both replies were sent
    sent = conversation_api_client.app.state.wa_client.sent
    assert len(sent) == 2
