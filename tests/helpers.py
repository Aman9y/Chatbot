from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.security.signature import sign_body
from app.services.llm.base import LLMClient, LLMResponse

FIXTURES = Path(__file__).parent / "fixtures"

APP_SECRET = "test-app-secret"


class HangingLLMClient(LLMClient):
    """An LLM client whose ``complete()`` never returns on its own — the
    production shape of the 2026-09-12 incidents (a Celery task stuck with no
    exception, no timeout, nothing). Used to prove a call site is actually
    bounded by ``complete_with_timeout`` rather than awaiting forever.

    ``delay_seconds`` is intentionally long relative to any timeout used in a
    test — the test passes only if ``complete_with_timeout`` cuts the wait
    short well before this delay would ever elapse.
    """

    provider = "hanging"

    def __init__(self, delay_seconds: float = 3600.0) -> None:
        self.delay_seconds = delay_seconds
        self.calls = 0

    async def complete(self, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        await asyncio.sleep(self.delay_seconds)
        raise AssertionError("HangingLLMClient.complete() should have been cancelled")


def load_fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads(load_fixture_bytes(name))


def signed_headers(raw_body: bytes, *, secret: str = APP_SECRET) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": sign_body(secret, raw_body),
    }


def as_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode("utf-8")


def inbound_text_payload(
    *,
    wamid: str,
    from_: str = "919812345670",
    text: str = "Hello",
    timestamp: str = "1725700000",
    name: str | None = "Test User",
) -> dict[str, Any]:
    contacts = [{"profile": {"name": name}, "wa_id": from_}] if name else []
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "919000000000",
                                "phone_number_id": "PNID",
                            },
                            "contacts": contacts,
                            "messages": [
                                {
                                    "from": from_,
                                    "id": wamid,
                                    "timestamp": timestamp,
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def status_payload(
    *,
    wamid: str,
    status: str,
    recipient: str = "919812345672",
    timestamp: str = "1725700200",
    conversation_id: str | None = "CONV1",
    pricing_category: str | None = None,
    billable: bool | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": wamid,
        "status": status,
        "timestamp": timestamp,
        "recipient_id": recipient,
    }
    if conversation_id:
        entry["conversation"] = {"id": conversation_id, "origin": {"type": "marketing"}}
    if pricing_category is not None or billable is not None:
        entry["pricing"] = {
            "category": pricing_category,
            "billable": billable,
            "pricing_model": "PMP",
            "type": "regular",
        }
    if errors:
        entry["errors"] = errors
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "919000000000",
                                "phone_number_id": "PNID",
                            },
                            "statuses": [entry],
                        },
                    }
                ],
            }
        ],
    }
