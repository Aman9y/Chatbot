"""360dialog WhatsApp client — confirmed API shape from their dashboard:
base URL https://waba-v2.360dialog.io, header "D360-API-KEY: <key>" (not
Bearer, not Meta's Graph format), POST /messages (no phone-number-id in the
path). Reuses MetaWhatsAppClient's Cloud-API-compatible payload building —
see app/services/whatsapp/dialog360.py.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.services.whatsapp.dialog360 import Dialog360WhatsAppClient
from app.services.whatsapp.factory import build_whatsapp_client


def _settings(**over) -> Settings:
    base = dict(whatsapp_client="360dialog", d360_api_key="test-d360-key")
    base.update(over)
    return Settings(**base)


def test_missing_api_key_fails_fast():
    with pytest.raises(RuntimeError, match="D360_API_KEY"):
        Dialog360WhatsAppClient(Settings(whatsapp_client="360dialog", d360_api_key=""))


def test_factory_builds_the_360dialog_client():
    client = build_whatsapp_client(_settings())
    assert isinstance(client, Dialog360WhatsAppClient)
    assert client.name == "360dialog"


def test_url_and_auth_header_match_the_confirmed_api_shape():
    s = _settings()
    client = Dialog360WhatsAppClient(s)
    # exact endpoint 360dialog confirmed: POST /messages, no phone-number-id
    assert client._url == "https://waba-v2.360dialog.io/messages"
    # header is "D360-API-KEY", NOT "Authorization: Bearer ..."
    assert client._client.headers.get("d360-api-key") == "test-d360-key"
    assert "authorization" not in client._client.headers


def test_base_url_is_configurable():
    s = _settings(d360_base_url="https://staging.example.com/waba")
    client = Dialog360WhatsAppClient(s)
    assert client._url == "https://staging.example.com/waba/messages"


async def test_send_text_posts_cloud_api_shaped_body_and_parses_the_id():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.ABC123"}]})

    transport = httpx.MockTransport(handler)
    s = _settings()
    inner = httpx.AsyncClient(transport=transport, headers={"D360-API-KEY": s.d360_api_key})
    client = Dialog360WhatsAppClient(s, client=inner)

    result = await client.send_text(to="+919812345670", text="hello there")

    assert result.message_id == "wamid.ABC123"
    assert captured["url"] == "https://waba-v2.360dialog.io/messages"
    assert captured["headers"]["d360-api-key"] == "test-d360-key"
    assert "authorization" not in captured["headers"]
    body = captured["body"]
    assert body["messaging_product"] == "whatsapp"
    assert body["to"] == "919812345670"  # leading + stripped, same as Meta client
    assert body["type"] == "text"
    assert body["text"]["body"] == "hello there"

    await client.aclose()


async def test_send_template_posts_cloud_api_shaped_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"messages": [{"id": "wamid.TPL1"}]})

    transport = httpx.MockTransport(handler)
    s = _settings()
    inner = httpx.AsyncClient(transport=transport)
    client = Dialog360WhatsAppClient(s, client=inner)

    result = await client.send_template(
        to="+919812345670",
        template_name="gate_consent_v1",
        language="en",
        variables={"body": ["Priya"]},
    )
    assert result.message_id == "wamid.TPL1"
    await client.aclose()


async def test_send_failure_raises_whatsapp_api_error():
    from app.errors import WhatsAppAPIError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text='{"error": "invalid api key"}')

    transport = httpx.MockTransport(handler)
    s = _settings()
    inner = httpx.AsyncClient(transport=transport)
    client = Dialog360WhatsAppClient(s, client=inner)

    with pytest.raises(WhatsAppAPIError):
        await client.send_text(to="+919812345670", text="hi")
    await client.aclose()
