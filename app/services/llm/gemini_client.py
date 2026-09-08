"""Google Gemini adapter — uses the official `google-genai` SDK.

The API key is read from the environment only (``GEMINI_API_KEY`` via
``Settings.gemini_api_key``). It is never logged, printed, or written anywhere:
this module keeps the key inside the SDK client instance and nothing else.

Scoping note: a free-tier AI Studio key is fine for testing with SYNTHETIC
leads. Production uses a paid key set through the SAME ``GEMINI_API_KEY``
variable — no code change.
"""

from __future__ import annotations

import time

from app.config import Settings
from app.logging_config import get_logger
from app.services.llm.base import LLMClient, LLMError, LLMMessage, LLMResponse

logger = get_logger(__name__)


class GeminiLLMClient(LLMClient):
    provider = "gemini"

    def __init__(self, settings: Settings) -> None:
        from google import genai

        self._settings = settings
        key = settings.gemini_api_key or None
        if not key:
            # Fail fast + loud rather than surfacing an opaque SDK auth error.
            raise LLMError(
                "LLM_PROVIDER=gemini but GEMINI_API_KEY is not set in the environment"
            )
        # vertexai=False -> use the Gemini Developer API with the API key, never
        # fall back to ambient Google Cloud / Vertex credentials.
        self._client = genai.Client(api_key=key, vertexai=False)

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
        )

        start = time.perf_counter()
        try:
            resp = await self._client.aio.models.generate_content(
                model=model, contents=contents, config=config
            )
        except errors.APIError as exc:  # pragma: no cover - network
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
