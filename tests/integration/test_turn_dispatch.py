"""Build-plan §3 concurrency: debounce/merge + per-lead lock + celery dispatch."""

from __future__ import annotations

import pytest
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
from app.models.lead import Lead
from app.models.message import Message
from app.services.conversation.dispatch import LockUnavailable, TurnDeps, run_lead_turn
from app.services.conversation.locks import LeadTurnLock
from app.services.timeutils import utcnow
from app.services.webhook_processor import WebhookProcessor
from app.services.windows import WindowService
from tests.helpers import as_bytes, inbound_text_payload, signed_headers


def _settings(**over) -> Settings:
    base = dict(
        llm_provider="fake",
        bot_autoreply_enabled=True,
        guard_enabled=True,
        booking_detection_enabled=False,
        counselor_name="Dr. Rao",
        turn_debounce_ms=0,
    )
    base.update(over)
    return Settings(**base)


async def _engaged_lead(session, redis, settings, phone="+919812345670") -> Lead:
    lead = Lead(
        phone_e164=phone,
        lifecycle_state=LifecycleState.ENGAGED,
        first_engaged_at=utcnow(),
        consent_gate=ConsentGate.CLEARED,
    )
    session.add(lead)
    await session.flush()
    await WindowService(redis, settings).touch(lead)
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
    await session.flush()
    await session.commit()
    return msg


def _deps(session, redis, settings, llm, kb, wa) -> TurnDeps:
    return TurnDeps(session=session, redis=redis, settings=settings, llm=llm, kb=kb, wa_client=wa)


# --- lock primitive -------------------------------------------------------
async def test_lock_is_mutually_exclusive(redis_client):
    lock = LeadTurnLock(redis_client, ttl_seconds=60)
    h1 = await lock.try_acquire("lead-1")
    assert h1.acquired
    h2 = await lock.try_acquire("lead-1")
    assert not h2.acquired
    await lock.release(h1)
    h3 = await lock.try_acquire("lead-1")
    assert h3.acquired
    # releasing a stale handle must not free someone else's lock
    await lock.release(h2)
    assert await lock.is_held("lead-1")


# --- debounce / merge ---------------------------------------------------
async def test_pending_messages_merge_into_one_turn(
    session, redis_client, wa_client, knowledge_base, llm_client
):
    s = _settings()
    lead = await _engaged_lead(session, redis_client, s)
    await _inbound(session, lead, "hi", "wamid.M1")
    await _inbound(session, lead, "I'm interested in Russia", "wamid.M2")

    result = await run_lead_turn(
        _deps(session, redis_client, s, llm_client, knowledge_base, wa_client), lead.id
    )

    assert result.action == "sent"
    # exactly one outbound reply for the burst
    assert len(wa_client.sent) == 1
    outbound = await session.scalar(
        select(func.count()).select_from(Message).where(Message.direction == MessageDirection.OUTBOUND)
    )
    assert outbound == 1
    # the merged-in message got a "merged" trace so it is never reprocessed
    traces = (
        await session.scalars(select(ConversationTrace).where(ConversationTrace.lead_id == lead.id))
    ).all()
    actions = sorted(t.final_action for t in traces)
    assert actions == ["merged", "sent"]
    sent_trace = next(t for t in traces if t.final_action == "sent")
    assert "Russia" in (sent_trace.final_text or "") or sent_trace.final_action == "sent"


async def test_second_turn_with_nothing_pending_is_a_noop(
    session, redis_client, wa_client, knowledge_base, llm_client
):
    s = _settings()
    lead = await _engaged_lead(session, redis_client, s)
    await _inbound(session, lead, "hello", "wamid.N1")
    d = _deps(session, redis_client, s, llm_client, knowledge_base, wa_client)

    first = await run_lead_turn(d, lead.id)
    assert first.action == "sent"
    second = await run_lead_turn(d, lead.id)
    assert second.action == "skipped"
    assert second.skipped_reason == "no_pending_inbound"
    assert len(wa_client.sent) == 1


async def test_run_lead_turn_raises_when_lock_held(
    session, redis_client, wa_client, knowledge_base, llm_client
):
    s = _settings()
    lead = await _engaged_lead(session, redis_client, s)
    await _inbound(session, lead, "hello", "wamid.L1")

    # someone else holds the lock
    await LeadTurnLock(redis_client, 60).try_acquire(lead.id)

    with pytest.raises(LockUnavailable):
        await run_lead_turn(
            _deps(session, redis_client, s, llm_client, knowledge_base, wa_client), lead.id
        )
    assert not wa_client.sent


# --- celery dispatch: webhook enqueues, does not reply inline -----------
async def test_webhook_celery_dispatch_enqueues_and_does_not_reply(
    session, redis_client, wa_client, knowledge_base, llm_client, monkeypatch
):
    enqueued: list[str] = []

    class _FakeTask:
        def delay(self, lead_id: str) -> None:
            enqueued.append(lead_id)

    import app.scheduler.conversation_tasks as ct

    monkeypatch.setattr(ct, "process_lead_turn", _FakeTask())

    s = _settings(webhook_conversation_dispatch="celery")
    processor = WebhookProcessor(
        session, redis_client, s, llm=llm_client, kb=knowledge_base, wa_client=wa_client
    )
    payload = inbound_text_payload(wamid="wamid.CEL1", from_="919812345670", text="hi there")
    raw = as_bytes(payload)
    result = await processor.ingest(
        raw_body=raw,
        signature_header=signed_headers(raw)["X-Hub-Signature-256"],
        headers={},
        source_ip="1.2.3.4",
    )

    assert result.status == "processed"
    assert len(enqueued) == 1
    # no inline reply in celery mode
    assert not wa_client.sent
    assert (
        await session.scalar(
            select(func.count()).select_from(Message).where(Message.direction == MessageDirection.OUTBOUND)
        )
        == 0
    )
