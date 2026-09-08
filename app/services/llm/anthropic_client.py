"""Anthropic (Claude) adapter — uses the official `anthropic` SDK."""

from __future__ import annotations

import time
from typing import Any

from app.config import Settings
from app.logging_config import get_logger
from app.services.llm.base import LLMClient, LLMError, LLMMessage, LLMResponse

logger = get_logger(__name__)


class AnthropicLLMClient(LLMClient):
    provider = "anthropic"

    def __init__(self, settings: Settings) -> None:
        from anthropic import AsyncAnthropic

        self._settings = settings
        self._client = AsyncAnthropic(
            api_key=settings.anthropic_api_key or None,
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
        from anthropic import APIError

        wire_messages = [{"role": m.role, "content": m.content} for m in messages]
        kwargs: dict[str, Any] = {
            "model": model,
            "system": system,
            "messages": wire_messages,
            "max_tokens": max_output_tokens,
        }
        # Sampling params are rejected (400) on the Claude 5 family; only send a
        # temperature for models that still accept it.
        if temperature is not None and not _is_claude5(model):
            kwargs["temperature"] = temperature

        # Effort + adaptive thinking on models that support it (not Haiku).
        if "haiku" not in model.lower():
            kwargs["thinking"] = {"type": "adaptive"}
            if self._settings.anthropic_effort:
                kwargs["output_config"] = {"effort": self._settings.anthropic_effort}

        start = time.perf_counter()
        try:
            resp = await self._client.messages.create(**kwargs)
        except APIError as exc:  # pragma: no cover - network
            raise LLMError(f"anthropic call failed: {exc}") from exc
        latency_ms = (time.perf_counter() - start) * 1000

        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        ).strip()
        usage = resp.usage
        return LLMResponse(
            text=text,
            model=model,
            provider=self.provider,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            latency_ms=latency_ms,
            finish_reason=resp.stop_reason,
            raw={"id": getattr(resp, "id", None), "stop_reason": resp.stop_reason},
        )

    async def aclose(self) -> None:
        await self._client.close()


def _is_claude5(model: str) -> bool:
    m = model.lower()
    return any(tag in m for tag in ("opus-5", "sonnet-5", "fable-5", "opus-4-8", "opus-4-7"))
