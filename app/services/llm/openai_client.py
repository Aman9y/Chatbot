"""OpenAI adapter — uses the official `openai` SDK (Chat Completions)."""

from __future__ import annotations

import time
from typing import Any

from app.config import Settings
from app.logging_config import get_logger
from app.services.llm.base import LLMClient, LLMError, LLMMessage, LLMResponse

logger = get_logger(__name__)


class OpenAILLMClient(LLMClient):
    provider = "openai"

    def __init__(self, settings: Settings) -> None:
        from openai import AsyncOpenAI

        self._settings = settings
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key or None,
            timeout=settings.llm_timeout_seconds,
            max_retries=2,
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
        from openai import OpenAIError

        wire: list[dict[str, Any]] = [{"role": "system", "content": system}]
        wire += [{"role": m.role, "content": m.content} for m in messages]

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": wire,
            "max_tokens": max_output_tokens,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        start = time.perf_counter()
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except OpenAIError as exc:  # pragma: no cover - network
            raise LLMError(f"openai call failed: {exc}") from exc
        latency_ms = (time.perf_counter() - start) * 1000

        choice = resp.choices[0]
        usage = resp.usage
        return LLMResponse(
            text=(choice.message.content or "").strip(),
            model=model,
            provider=self.provider,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=latency_ms,
            finish_reason=choice.finish_reason,
            raw={"id": getattr(resp, "id", None)},
        )

    async def aclose(self) -> None:
        await self._client.close()
