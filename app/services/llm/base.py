"""Provider-agnostic LLM client abstraction.

Two real adapters (Anthropic, OpenAI) + a deterministic fake. The engine passes
the model and knobs per call so the same client can serve both reply generation
and cheap classifier calls.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "assistant"]


@dataclass(frozen=True)
class LLMMessage:
    role: Role
    content: str


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    finish_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class LLMError(RuntimeError):
    """Raised when the provider call fails."""


class LLMClient(abc.ABC):
    provider: str = "base"

    @abc.abstractmethod
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
        """Return a single completion. `purpose` is advisory metadata (the fake
        client routes on it; real clients only log it)."""

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None
