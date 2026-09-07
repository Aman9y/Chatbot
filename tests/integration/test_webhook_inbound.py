from sqlalchemy import func, select

from app.models.enums import LifecycleState, MessageDirection
from app.models.lead import Lead
from app.models.message import Message
from app.models.webhook_event import WebhookEvent
from tests.helpers import as_bytes, inbound_text_payload, signed_headers


async def _post(api_client, payload):
    raw = as_bytes(payload)
    return await api_client.post("/webhook/whatsapp", content=raw, headers=signed_headers(raw))


async def test_inbound_creates_lead_and_message(api_client, session):
    payload = inbound_text_payload(
        wamid="wamid.T1", from_="919812345670", text="Hi about MBBS", name="Priya"
    )
    resp = await _post(api_client, payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"

    lead = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345670"))
    assert lead is not None
    assert lead.full_name == "Priya"
    assert lead.lifecycle_state == LifecycleState.ENGAGED
    assert lead.first_engaged_at is not None
    assert lead.last_inbound_at is not None
    assert lead.service_window_expires_at is not None

    msg = await session.scalar(select(Message).where(Message.wa_message_id == "wamid.T1"))
    assert msg.direction == MessageDirection.INBOUND
    assert msg.body == "Hi about MBBS"
    assert msg.lead_id == lead.id

    event = await session.scalar(select(WebhookEvent))
    assert event.processed is True
    assert event.signature_valid is True
    assert event.message_count == 1


async def test_window_open_in_redis(api_client, redis_client, session):
    payload = inbound_text_payload(wamid="wamid.T2", from_="919812345671", text="hello")
    await _post(api_client, payload)
    lead = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345671"))
    raw = await redis_client.get(f"wa:window:{lead.id}")
    assert raw is not None


async def test_inbound_from_new_number_then_reply_stays_single_lead(api_client, session):
    await _post(api_client, inbound_text_payload(wamid="wamid.T3a", from_="919812345672", text="hi"))
    await _post(api_client, inbound_text_payload(wamid="wamid.T3b", from_="919812345672", text="still me"))
    count = await session.scalar(
        select(func.count()).select_from(Lead).where(Lead.phone_e164 == "+919812345672")
    )
    assert count == 1
    msgs = await session.scalar(select(func.count()).select_from(Message))
    assert msgs == 2


async def test_non_message_change_field_ignored(api_client, session):
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"id": "W", "changes": [{"field": "account_update", "value": {"event": "x"}}]}],
    }
    resp = await _post(api_client, payload)
    assert resp.status_code == 200
    assert await session.scalar(select(func.count()).select_from(Lead)) == 0
