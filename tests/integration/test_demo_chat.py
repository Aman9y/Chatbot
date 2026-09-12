"""The local WhatsApp-lookalike demo endpoint (app/api/routes_demo.py).

Not the production Meta webhook path — a separate, explicitly-gated surface
for showing the bot without a WhatsApp connection. These tests confirm: it's
off unless enabled, it refuses to run against a real WhatsApp client even when
enabled, and when it does run it goes through the same engine/guard/KB the
webhook path uses, with the consent+age gate pre-cleared.
"""

from __future__ import annotations

from sqlalchemy import select

from app.api import deps
from app.config import Settings
from app.models.enums import ConsentGate, LifecycleState
from app.models.lead import Lead


def _enable_demo(client, **overrides):
    kwargs = {"demo_enabled": True, "whatsapp_client": "fake"}
    kwargs.update(overrides)
    settings = Settings(**kwargs)
    client.app.dependency_overrides[deps.get_app_settings] = lambda: settings
    return settings


async def test_demo_disabled_by_default(conversation_api_client):
    resp = await conversation_api_client.get("/demo")
    assert resp.status_code == 404

    resp = await conversation_api_client.post(
        "/demo/chat", json={"phone": "+919812345670", "message": "hi"}
    )
    assert resp.status_code == 404
    assert "DEMO_ENABLED" in resp.json()["detail"]


async def test_demo_refuses_a_non_fake_whatsapp_client(conversation_api_client):
    # even with demo_enabled=True, never let it run against a real WA client
    _enable_demo(conversation_api_client, whatsapp_client="meta")
    resp = await conversation_api_client.post(
        "/demo/chat", json={"phone": "+919812345670", "message": "hi"}
    )
    assert resp.status_code == 409
    assert "WHATSAPP_CLIENT=fake" in resp.json()["detail"]


async def test_demo_index_served_when_enabled(conversation_api_client):
    _enable_demo(conversation_api_client)
    resp = await conversation_api_client.get("/demo")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Stellar AI" in resp.text


async def test_demo_chat_runs_the_real_engine_and_skips_the_gate(
    conversation_api_client, session
):
    _enable_demo(conversation_api_client)
    resp = await conversation_api_client.post(
        "/demo/chat",
        json={"phone": "+919812399001", "message": "hi, tell me about MBBS abroad"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "sent"
    assert body["reply"]  # the fake LLM's canned reply, produced by the real engine

    lead = await session.scalar(
        select(Lead).where(Lead.phone_e164 == "+919812399001")
    )
    assert lead is not None
    # gate skipped (like `simulate`), lead engaged, not sitting in gate_age_ask
    assert lead.consent_gate == ConsentGate.CLEARED
    assert lead.lifecycle_state == LifecycleState.ENGAGED


async def test_demo_chat_reuses_the_same_lead_across_turns(
    conversation_api_client, session
):
    _enable_demo(conversation_api_client)
    phone = "+919812399002"
    await conversation_api_client.post(
        "/demo/chat", json={"phone": phone, "message": "my NEET score is 200"}
    )
    await conversation_api_client.post(
        "/demo/chat", json={"phone": phone, "message": "hello again"}
    )
    leads = (
        await session.scalars(select(Lead).where(Lead.phone_e164 == phone))
    ).all()
    assert len(leads) == 1  # one lead, not one per turn


async def test_demo_chat_rejects_bad_payload(conversation_api_client):
    _enable_demo(conversation_api_client)
    resp = await conversation_api_client.post(
        "/demo/chat", json={"phone": "+919812345670", "message": ""}
    )
    assert resp.status_code == 422


# --- director review: fixed opening message sent before anything is typed --
async def test_demo_start_sends_the_fixed_opener_for_a_new_lead(
    conversation_api_client, session
):
    s = _enable_demo(conversation_api_client)
    resp = await conversation_api_client.post(
        "/demo/start", json={"phone": "+919812399010"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "sent"
    assert body["reply"] == s.opening_message

    lead = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812399010"))
    assert lead is not None
    assert lead.consent_gate == ConsentGate.CLEARED
    assert lead.lifecycle_state == LifecycleState.ENGAGED


async def test_demo_start_is_a_noop_once_the_lead_has_messages(conversation_api_client):
    _enable_demo(conversation_api_client)
    phone = "+919812399011"
    first = await conversation_api_client.post("/demo/start", json={"phone": phone})
    assert first.json()["action"] == "sent"

    # a reload / repeated call must never resend the opener
    second = await conversation_api_client.post("/demo/start", json={"phone": phone})
    assert second.json() == {"reply": None, "action": "already_started"}

    # nor once the tester has actually started chatting
    third_phone = "+919812399012"
    await conversation_api_client.post(
        "/demo/chat", json={"phone": third_phone, "message": "hi"}
    )
    after_chat = await conversation_api_client.post(
        "/demo/start", json={"phone": third_phone}
    )
    assert after_chat.json()["action"] == "already_started"


async def test_demo_start_disabled_by_default(conversation_api_client):
    resp = await conversation_api_client.post(
        "/demo/start", json={"phone": "+919812345670"}
    )
    assert resp.status_code == 404
