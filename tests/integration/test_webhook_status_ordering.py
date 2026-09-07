from sqlalchemy import select

from app.models.enums import MessageDirection, MessageStatus, MessageType
from app.models.message import Message
from tests.helpers import as_bytes, signed_headers, status_payload


async def _post(api_client, payload):
    raw = as_bytes(payload)
    return await api_client.post("/webhook/whatsapp", content=raw, headers=signed_headers(raw))


async def _seed_outbound(session, wamid: str, phone: str = "+919812345672") -> None:
    from app.models.lead import Lead

    lead = Lead(phone_e164=phone)
    session.add(lead)
    await session.flush()
    session.add(
        Message(
            lead_id=lead.id,
            direction=MessageDirection.OUTBOUND,
            wa_message_id=wamid,
            message_type=MessageType.TEMPLATE,
            status=MessageStatus.SENT,
            status_history=[{"status": "sent", "at": "2024-01-01T00:00:00", "source": "outbound"}],
        )
    )
    await session.commit()


async def test_out_of_order_status_keeps_furthest(api_client, session):
    await _seed_outbound(session, "wamid.OOO1")

    await _post(api_client, status_payload(wamid="wamid.OOO1", status="read", timestamp="1725700300"))
    await _post(
        api_client, status_payload(wamid="wamid.OOO1", status="delivered", timestamp="1725700200")
    )

    msg = await session.scalar(select(Message).where(Message.wa_message_id == "wamid.OOO1"))
    assert msg.status == MessageStatus.READ
    recorded = {h["status"] for h in msg.status_history}
    assert {"sent", "read", "delivered"} <= recorded


async def test_status_for_unknown_message_creates_placeholder(api_client, session):
    await _post(
        api_client,
        status_payload(wamid="wamid.UNKNOWN1", status="delivered", recipient="919812345690"),
    )
    msg = await session.scalar(select(Message).where(Message.wa_message_id == "wamid.UNKNOWN1"))
    assert msg is not None
    assert msg.direction == MessageDirection.OUTBOUND
    assert msg.status == MessageStatus.DELIVERED
    assert msg.counterparty_phone == "+919812345690"


async def test_failed_status_records_error(api_client, session):
    await _seed_outbound(session, "wamid.FAIL1")
    await _post(
        api_client,
        status_payload(
            wamid="wamid.FAIL1",
            status="failed",
            errors=[
                {
                    "code": 131047,
                    "title": "Re-engagement message",
                    "error_data": {"details": "Message failed to send outside 24h window"},
                }
            ],
        ),
    )
    msg = await session.scalar(select(Message).where(Message.wa_message_id == "wamid.FAIL1"))
    assert msg.status == MessageStatus.FAILED
    assert msg.error_code == "131047"
    assert "24h" in msg.error_detail


async def test_pricing_captured(api_client, session):
    await _seed_outbound(session, "wamid.PRICE1")
    await _post(
        api_client,
        status_payload(
            wamid="wamid.PRICE1",
            status="delivered",
            pricing_category="marketing",
            billable=True,
        ),
    )
    msg = await session.scalar(select(Message).where(Message.wa_message_id == "wamid.PRICE1"))
    assert msg.pricing_category == "marketing"
    assert msg.billable is True
