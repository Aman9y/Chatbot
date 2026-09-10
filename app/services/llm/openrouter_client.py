"""OpenRouter adapter — OpenAI-compatible chat completions via openrouter.ai.

Used to reach ``google/gemini-3.7-flash`` through OpenRouter instead of Google's
API directly. OpenRouter speaks the OpenAI Chat Completions wire format, so this
reuses ``OpenAILLMClient.complete`` unchanged and only swaps the base URL, the
API key source, and the provider label.

DATA-HANDLING NOTICE (not just a routing detail)
------------------------------------------------
With ``LLM_PROVIDER=openrouter`` every inbound lead message, the assembled
system prompt (which includes the lead's extracted profile), and the full recent
conversation history are sent to **OpenRouter's servers**, which then forward the
request to the **upstream model host** (Google, for the gemini route). That is
two external processors in the path where there was previously one. Before this
provider is pointed at real leads:

  * OpenRouter's stated retention / logging policy for API traffic must be read
    and accepted for lead PII (name, phone context, NEET score, city, budget).
  * ``OPENROUTER_DATA_POLICY_CONFIRMED=true`` must be set to acknowledge it —
    ``leadbot check-config`` flags the provider as unresolved until then.

See ``docs/llm-data-handling.md`` for the full data path and the policy record.

The API key is read from ``OPENROUTER_API_KEY`` (via ``Settings``) only. It lives
inside the SDK client instance and is never logged, printed, or persisted.
"""

from __future__ import annotations

from app.config import Settings
from app.logging_config import get_logger
from app.services.llm.base import LLMError
from app.services.llm.openai_client import OpenAILLMClient

logger = get_logger(__name__)


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
