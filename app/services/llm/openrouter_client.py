"""OpenRouter adapter — OpenAI-compatible chat completions via openrouter.ai.

Used to reach ``google/gemini-3.7-flash`` through OpenRouter instead of Google's
API directly. OpenRouter speaks the OpenAI Chat Completions wire format, so this
reuses ``OpenAILLMClient.complete`` unchanged and only swaps the base URL, the
API key source, the provider label, and attaches a **routing safety pin** to
every request.

DATA-HANDLING NOTICE (not just a routing detail)
------------------------------------------------
With ``LLM_PROVIDER=openrouter`` every inbound lead message, the assembled
system prompt (which includes the lead's extracted profile), and the full recent
conversation history are sent to **OpenRouter's servers**, which then forward the
request to an **upstream model host**. That is two external processors in the
path where there was previously one.

To bound where lead data can go, every request carries provider-routing
constraints built from config (see ``Settings.openrouter_*``):

  * ``provider.only``          -> allow-list of upstream endpoints
                                  (default: ``google-vertex`` only — Vertex does
                                  not train on API data; Google AI Studio's terms
                                  can);
  * ``provider.data_collection``-> ``deny`` refuses any endpoint that stores data
                                  non-transiently;
  * ``provider.allow_fallbacks``-> ``false`` so a blocked route errors loudly
                                  instead of silently going elsewhere;
  * ``zdr: true``              -> request zero-data-retention handling.

If OpenRouter cannot honour the pin it returns an error and the turn falls back —
it will not quietly route lead data to an un-vetted endpoint.
``OPENROUTER_DATA_POLICY_CONFIRMED`` must still be set true (after proving the
pin with a live call) for the engine to treat the provider as launch-ready;
``leadbot check-config`` flags it until then. See ``docs/llm-data-handling.md``.

The API key is read from ``OPENROUTER_API_KEY`` (via ``Settings``) only. It lives
inside the SDK client instance and is never logged, printed, or persisted.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.logging_config import get_logger
from app.services.llm.base import LLMError
from app.services.llm.openai_client import OpenAILLMClient

logger = get_logger(__name__)


def build_routing_extra_body(settings: Settings) -> dict[str, Any]:
    """The provider-routing constraints attached to every OpenRouter request."""

    provider: dict[str, Any] = {}
    only = [p.strip() for p in settings.openrouter_provider_only.split(",") if p.strip()]
    if only:
        provider["only"] = only
    if settings.openrouter_data_collection:
        provider["data_collection"] = settings.openrouter_data_collection
    # allow_fallbacks defaults true on OpenRouter; only send it when pinning off.
    if not settings.openrouter_allow_fallbacks:
        provider["allow_fallbacks"] = False

    body: dict[str, Any] = {}
    if provider:
        body["provider"] = provider
    if settings.openrouter_require_zdr:
        body["zdr"] = True
    return body


class OpenRouterLLMClient(OpenAILLMClient):
    provider = "openrouter"

    def __init__(self, settings: Settings) -> None:
        from openai import AsyncOpenAI

        self._settings = settings
        key = settings.openrouter_api_key or None
        if not key:
            # Fail fast + loud rather than surfacing an opaque SDK auth error.
            raise LLMError(
                "LLM_PROVIDER=openrouter but OPENROUTER_API_KEY is not set in the "
                "environment"
            )

        # Optional attribution headers OpenRouter surfaces on its dashboard.
        headers: dict[str, str] = {}
        if settings.openrouter_app_url.strip():
            headers["HTTP-Referer"] = settings.openrouter_app_url.strip()
        if settings.openrouter_app_title.strip():
            headers["X-Title"] = settings.openrouter_app_title.strip()

        # Routing safety pin — attached to every request by OpenAILLMClient.complete.
        self._extra_body = build_routing_extra_body(settings) or None
        logger.info("openrouter routing pin: %s", self._extra_body)

        if not settings.openrouter_data_policy_confirmed:
            logger.warning(
                "openrouter provider active but OPENROUTER_DATA_POLICY_CONFIRMED is "
                "false — lead conversation data is being routed through OpenRouter "
                "(then the upstream host) without the data-handling policy confirmed"
            )

        self._client = AsyncOpenAI(
            api_key=key,
            base_url=settings.openrouter_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=2,
            default_headers=headers or None,
        )
