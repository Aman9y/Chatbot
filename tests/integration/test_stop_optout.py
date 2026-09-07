import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.errors import OptOutViolation, OutreachBlocked
from app.models.consent import ConsentRecord
from app.models.enums import ConsentStatus, LifecycleState, MessageType, SentBy
from app.models.lead import Lead
from app.models.message import Message
from app.services import messages as messages_service
from app.services.outreach import OutreachService
from tests.helpers import as_bytes, inbound_text_payload, signed_headers


async def _post(api_client, payload):
    raw = as_bytes(payload)
    return await api_client.post("/webhook/whatsapp", content=raw, headers=signed_headers(raw))


async def test_stop_message_opts_lead_out(api_client, session, redis_client):
    await _post(api_client, inbound_text_payload(wamid="wamid.S1", from_="919812345670", text="hi"))
    lead = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345670"))
    assert lead.lifecycle_state == LifecycleState.ENGAGED

    await _post(
        api_client, inbound_text_payload(wamid="wamid.S2", from_="919812345670", text="please STOP")
    )
    await session.refresh(lead)
    assert lead.lifecycle_state == LifecycleState.OPTED_OUT
    assert lead.consent_status == ConsentStatus.OPTED_OUT
    assert lead.opted_out_at is not None

    rec = await session.scalar(
        select(ConsentRecord)
        .where(ConsentRecord.lead_id == lead.id, ConsentRecord.status == ConsentStatus.OPTED_OUT)
    )
    assert rec.method.value == "inbound_stop_keyword"

    # window closed
    assert await redis_client.get(f"wa:window:{lead.id}") is None


async def test_stop_message_is_persisted(api_client, session):
    await _post(
        api_client, inbound_text_payload(wamid="wamid.S3", from_="919812345671", text="unsubscribe")
    )
    msg = await session.scalar(select(Message).where(Message.wa_message_id == "wamid.S3"))
    assert msg is not None
    assert msg.body == "unsubscribe"


async def test_outreach_blocked_after_opt_out(api_client, session, redis_client, wa_client):
    await _post(
        api_client, inbound_text_payload(wamid="wamid.S4", from_="919812345672", text="stop")
    )
    lead = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345672"))

    service = OutreachService(session, redis_client, get_settings(), wa_client)
    decision = service.evaluate(lead)
    assert decision.allowed is False
    assert "lead_opted_out" in decision.hard_blocks

    with pytest.raises(OutreachBlocked):
        await service.send_template(lead, template_name="reengage", language="en")


async def test_persist_outbound_hard_refuses_opted_out(api_client, session):
    await _post(
        api_client, inbound_text_payload(wamid="wamid.S5", from_="919812345673", text="STOP")
    )
    lead = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345673"))
    with pytest.raises(OptOutViolation):
        await messages_service.persist_outbound(
            session, lead, message_type=MessageType.TEXT, body="hello?", sent_by=SentBy.BOT
        )


async def test_message_from_opted_out_lead_does_not_reengage(api_client, session):
    await _post(api_client, inbound_text_payload(wamid="wamid.S6", from_="919812345674", text="stop"))
    await _post(
        api_client,
        inbound_text_payload(wamid="wamid.S7", from_="919812345674", text="actually tell me more"),
    )
    lead = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345674"))
    assert lead.lifecycle_state == LifecycleState.OPTED_OUT
    # both messages persisted
    n = await session.scalar(
        select(func.count()).select_from(Message).where(Message.lead_id == lead.id)
    )
    assert n == 2
