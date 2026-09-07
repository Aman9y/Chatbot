from sqlalchemy import func, select

from app.models.lead import Lead
from app.models.message import Message
from app.models.webhook_event import WebhookEvent
from tests.helpers import as_bytes, inbound_text_payload, signed_headers


async def _post(api_client, payload):
    raw = as_bytes(payload)
    return await api_client.post("/webhook/whatsapp", content=raw, headers=signed_headers(raw))


async def test_identical_payload_processed_once(api_client, session):
    payload = inbound_text_payload(wamid="wamid.DUP1", from_="919812345670", text="hi")

    r1 = await _post(api_client, payload)
    r2 = await _post(api_client, payload)

    assert r1.json()["status"] == "processed"
    assert r2.json()["status"] == "duplicate"

    assert await session.scalar(select(func.count()).select_from(Message)) == 1
    assert await session.scalar(select(func.count()).select_from(Lead)) == 1
    assert await session.scalar(select(func.count()).select_from(WebhookEvent)) == 1


async def test_same_wamid_in_different_envelope_not_duplicated(api_client, session):
    # same message id, different surrounding payload (e.g. Meta re-batches)
    await _post(api_client, inbound_text_payload(wamid="wamid.DUP2", text="first", from_="919812345671"))
    await _post(
        api_client,
        inbound_text_payload(wamid="wamid.DUP2", text="first", from_="919812345671", name="Renamed"),
    )
    msgs = await session.scalar(
        select(func.count()).select_from(Message).where(Message.wa_message_id == "wamid.DUP2")
    )
    assert msgs == 1
    # two distinct webhook events were still recorded for audit
    assert await session.scalar(select(func.count()).select_from(WebhookEvent)) == 2


async def test_unprocessed_event_is_reprocessed(api_client, session):
    """A previously-seen-but-not-processed event (e.g. a crash mid-processing)
    is retried rather than skipped as a duplicate."""

    import hashlib

    payload = inbound_text_payload(wamid="wamid.REPROC", from_="919812345672", text="hello")
    raw = as_bytes(payload)
    stale = WebhookEvent(
        event_hash=hashlib.sha256(raw).hexdigest(),
        signature_valid=True,
        processed=False,
        processing_error="simulated crash",
    )
    session.add(stale)
    await session.commit()

    resp = await api_client.post("/webhook/whatsapp", content=raw, headers=signed_headers(raw))
    assert resp.json()["status"] == "processed"

    await session.refresh(stale)
    assert stale.processed is True
    assert stale.processing_error is None
    assert await session.scalar(select(func.count()).select_from(Message)) == 1
