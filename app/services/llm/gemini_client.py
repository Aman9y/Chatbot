"""Google Gemini adapter — uses the official `google-genai` SDK.

The API key is read from the environment only (``GEMINI_API_KEY`` via
``Settings.gemini_api_key``). It is never logged, printed, or written anywhere:
this module keeps the key inside the SDK client instance and nothing else.

Scoping note: a free-tier AI Studio key is fine for testing with SYNTHETIC
leads. Production uses a paid key set through the SAME ``GEMINI_API_KEY``
variable — no code change.
"""

from __future__ import annotations

import asyncio
import time

from app.config import Settings
from app.logging_config import get_logger
from app.services.llm.base import LLMClient, LLMError, LLMMessage, LLMResponse

logger = get_logger(__name__)

# Transient upstream statuses worth a short retry (Gemini free tier throws 503
# "high demand" / 504 constantly; 429 is rate limiting).
_RETRYABLE = {429, 500, 503, 504}
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (0.0, 1.5, 4.0)


class GeminiLLMClient(LLMClient):
    provider = "gemini"

    def __init__(self, settings: Settings) -> None:
        from google import genai
        from google.genai import types

        self._settings = settings
        key = settings.gemini_api_key or None
        if not key:
            # Fail fast + loud rather than surfacing an opaque SDK auth error.
            raise LLMError(
                "LLM_PROVIDER=gemini but GEMINI_API_KEY is not set in the environment"
            )
        # vertexai=False -> use the Gemini Developer API with the API key, never
        # fall back to ambient Google Cloud / Vertex credentials.
        # timeout is in milliseconds; bounds the SDK's internal retry loop.
        self._client = genai.Client(
            api_key=key,
            vertexai=False,
            http_options=types.HttpOptions(
                timeout=int(settings.llm_timeout_seconds * 1000)
            ),
        )

    async def complete(
        self,
        *,
        system: str,
        messages: list[LLMMessage],
        model: str,
        max_output_tokens: int,
        temperature: float | None = None,
        json_mode: bool = False,
        purpose: str = "reply",
    ) -> LLMResponse:
        from google.genai import errors, types

        contents = [
            {
                "role": "model" if m.role == "assistant" else "user",
                "parts": [{"text": m.content}],
            }
            for m in messages
        ]
        config = types.GenerateContentConfig(
            system_instruction=system or None,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            response_mime_type="application/json" if json_mode else None,
            # we never pass tools — turn off automatic function calling so the
            # SDK doesn't warn on every call
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        start = time.perf_counter()
        resp = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = await self._client.aio.models.generate_content(
                    model=model, contents=contents, config=config
                )
                break
            except errors.APIError as exc:  # pragma: no cover - network
                code = getattr(exc, "code", None)
                if code in _RETRYABLE and attempt < _MAX_ATTEMPTS - 1:
                    logger.warning(
                        "gemini %s (%s); retry %d/%d",
                        code, purpose, attempt + 1, _MAX_ATTEMPTS - 1,
                    )
                    await asyncio.sleep(_BACKOFF_SECONDS[attempt + 1])
                    continue
                # exc carries a status code + message, not the API key.
                raise LLMError(f"gemini call failed: {exc}") from exc
        latency_ms = (time.perf_counter() - start) * 1000

        usage = getattr(resp, "usage_metadata", None)
        candidates = getattr(resp, "candidates", None) or []
        finish = getattr(candidates[0], "finish_reason", None) if candidates else None
        return LLMResponse(
            text=(getattr(resp, "text", None) or "").strip(),
            model=model,
            provider=self.provider,
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            latency_ms=latency_ms,
            finish_reason=str(finish) if finish is not None else None,
            raw={"id": getattr(resp, "response_id", None)},
        )

    async def aclose(self) -> None:
        try:
            await self._client.aio.aclose()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
