"""Deterministic in-memory LLM client for tests and offline development."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from typing import Any

from app.services.llm.base import LLMClient, LLMError, LLMMessage, LLMResponse

_DEFAULT_REPLY = (
    "Thanks for reaching out! MBBS abroad is a great option to explore. Our "
    "counsellor can help match a country to your profile — which are you "
    "considering, or would you like a few suggestions?"
)


class FakeLLMClient(LLMClient):
    provider = "fake"

    def __init__(
        self,
        *,
        reply: str = _DEFAULT_REPLY,
        replies: list[str] | None = None,
        json_responses: dict[str, Any] | list[dict[str, Any]] | None = None,
        responder: Callable[[str, list[LLMMessage], str], str] | None = None,
        raise_error: bool = False,
    ) -> None:
        self._default_reply = reply
        self._reply_queue: deque[str] = deque(replies or [])
        self._responder = responder
        self._raise_error = raise_error
        self.calls: list[dict[str, Any]] = []

        if isinstance(json_responses, list):
            self._json_queue: deque[dict[str, Any]] = deque(json_responses)
            self._json_by_purpose: dict[str, Any] = {}
        else:
            self._json_queue = deque()
            self._json_by_purpose = json_responses or {}

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
        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"), ""
        )
        self.calls.append(
            {
                "purpose": purpose,
                "model": model,
                "json_mode": json_mode,
                "system": system,
                "messages": [(m.role, m.content) for m in messages],
                "last_user": last_user,
            }
        )

        if self._raise_error:
            raise LLMError("fake llm error")

        if json_mode or purpose != "reply":
            text = self._next_json(purpose)
        elif self._responder is not None:
            text = self._responder(system, messages, purpose)
        elif self._reply_queue:
            text = self._reply_queue.popleft()
        else:
            text = self._default_reply

        return LLMResponse(
            text=text,
            model=model,
            provider=self.provider,
            input_tokens=len(system.split()) + sum(len(m.content.split()) for m in messages),
            output_tokens=len(text.split()),
            latency_ms=0.0,
            finish_reason="stop",
        )

    def _next_json(self, purpose: str) -> str:
        if self._json_queue:
            return json.dumps(self._json_queue.popleft())
        if purpose in self._json_by_purpose:
            return json.dumps(self._json_by_purpose[purpose])
        return "{}"
