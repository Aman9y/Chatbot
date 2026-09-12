"""Inbound webhook path when the source is 360dialog rather than direct Meta.

360dialog's WABA v2 forwards the same Cloud-API-shaped payload Meta itself
sends (entry[].changes[].value.messages[]/statuses[]) — the existing
WebhookEnvelope schema and WebhookProcessor parse it unchanged, no new
parsing needed. What DOES differ is security: 360dialog cannot produce
Meta's X-Hub-Signature-256 (there's no "your Meta app secret" in that setup),
so WEBHOOK_SIGNATURE_REQUIRED=false is how you tell the webhook not to
reject every 360dialog event, and WEBHOOK_BASIC_AUTH_USERNAME/PASSWORD is an
optional extra layer via credentials embedded in the webhook URL itself.
"""

from __future__ import annotations

import base64

from sqlalchemy import select

from app.api import deps
from app.config import Settings
from app.models.lead import Lead
from tests.helpers import as_bytes, inbound_text_payload


def _override(client, **kwargs) -> Settings:
    settings = Settings(**kwargs)
    client.app.dependency_overrides[deps.get_app_settings] = lambda: settings
    return settings


async def test_unsigned_payload_rejected_by_default(api_client):
    # default WEBHOOK_SIGNATURE_REQUIRED=true — an unsigned (e.g. 360dialog)
    # POST is rejected exactly like an invalid Meta signature today.
    payload = inbound_text_payload(wamid="wamid.D1", from_="919812345670")
    resp = await api_client.post(
        "/webhook/whatsapp",
        content=as_bytes(payload),
        headers={"Content-Type": "application/json"},  # no X-Hub-Signature-256
    )
    assert resp.status_code == 403
    assert resp.json()["status"] == "invalid_signature"


async def test_unsigned_payload_accepted_when_signature_not_required(api_client, session):
    _override(api_client, whatsapp_client="fake", webhook_signature_required=False)
    payload = inbound_text_payload(wamid="wamid.D2", from_="919812345671")
    resp = await api_client.post(
        "/webhook/whatsapp",
        content=as_bytes(payload),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"

    lead = await session.scalar(select(Lead).where(Lead.phone_e164 == "+919812345671"))
    assert lead is not None


async def test_basic_auth_required_when_configured(api_client, session):
    _override(
        api_client,
        whatsapp_client="fake",
        webhook_signature_required=False,
        webhook_basic_auth_username="d360",
        webhook_basic_auth_password="secret123",
    )
    payload = inbound_text_payload(wamid="wamid.D3", from_="919812345672")

    # no Authorization header at all -> rejected
    resp = await api_client.post(
        "/webhook/whatsapp",
        content=as_bytes(payload),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401

    # wrong credentials -> rejected
    bad = base64.b64encode(b"d360:wrong").decode()
    resp = await api_client.post(
        "/webhook/whatsapp",
        content=as_bytes(payload),
        headers={"Content-Type": "application/json", "Authorization": f"Basic {bad}"},
    )
    assert resp.status_code == 401

    # correct credentials -> accepted
    good = base64.b64encode(b"d360:secret123").decode()
    resp = await api_client.post(
        "/webhook/whatsapp",
        content=as_bytes(payload),
        headers={"Content-Type": "application/json", "Authorization": f"Basic {good}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"


async def test_basic_auth_not_enforced_when_unconfigured(api_client, session):
    # default settings have blank webhook_basic_auth_* -> no-op, existing
    # Meta-signature-only flow is unaffected.
    from tests.helpers import signed_headers

    payload = inbound_text_payload(wamid="wamid.D4", from_="919812345673")
    raw = as_bytes(payload)
    resp = await api_client.post(
        "/webhook/whatsapp", content=raw, headers=signed_headers(raw)
    )
    assert resp.status_code == 200


async def test_get_verification_also_respects_basic_auth(api_client):
    _override(
        api_client,
        whatsapp_client="fake",
        webhook_basic_auth_username="d360",
        webhook_basic_auth_password="secret123",
        meta_verify_token="test-verify-token",
    )
    resp = await api_client.get(
        "/webhook/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "test-verify-token",
            "hub.challenge": "abc",
        },
    )
    assert resp.status_code == 401
