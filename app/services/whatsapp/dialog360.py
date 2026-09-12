"""360dialog adapter — WhatsApp Business API v2 (Cloud-API-compatible).

Confirmed directly from the 360dialog dashboard:
  * base URL   : https://waba-v2.360dialog.io
  * auth       : header "D360-API-KEY: <key>" — NOT a Bearer token, NOT
                 Meta's Graph API auth format
  * endpoint   : POST /messages — no phone-number-id in the path; the API key
                 itself is scoped to one WABA channel on 360dialog's side.

360dialog's WABA v2 product is a direct pass-through of the Meta Cloud API
message format — the JSON request/response bodies are otherwise identical to
what MetaWhatsAppClient already builds and parses. So this only swaps
construction (URL, auth header) and reuses every other method — the same
"only the transport differs" pattern OpenRouterLLMClient uses on top of
OpenAILLMClient (see app/services/llm/openrouter_client.py).

The API key is read from D360_API_KEY (via Settings) only. It lives inside
the httpx client instance and is never logged, printed, or persisted.
"""

from __future__ import annotations

import httpx

from app.config import Settings
from app.services.whatsapp.meta import MetaWhatsAppClient


class Dialog360WhatsAppClient(MetaWhatsAppClient):
    name = "360dialog"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        # Deliberately does NOT call super().__init__ — that enforces Meta's
        # own env vars (META_ACCESS_TOKEN / META_PHONE_NUMBER_ID), which don't
        # apply here. send_text / send_template / mark_read / _post / _to are
        # inherited unchanged: they only ever touch self._url and self._client.
        if not settings.d360_api_key:
            raise RuntimeError("WHATSAPP_CLIENT=360dialog requires D360_API_KEY")
        self._settings = settings
        self._url = f"{settings.d360_base_url.rstrip('/')}/messages"
        self._client = client or httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            headers={"D360-API-KEY": settings.d360_api_key},
        )
