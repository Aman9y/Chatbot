"""Provider-agnostic LLM client abstraction.

Two real adapters (Anthropic, OpenAI) + a deterministic fake. The engine passes
the model and knobs per call so the same client can serve both reply generation
and cheap classifier calls.
"""

from __future__ import annotations

import abc
import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

from app.logging_config import get_logger, log_extra

logger = get_logger(__name__)

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


async def complete_with_timeout(
    client: LLMClient, *, timeout: float | None, **kwargs: Any
) -> LLMResponse:
    """``client.complete(**kwargs)``, but bounded regardless of whatever
    timeout handling (or lack of it) the specific provider SDK does
    internally.

    Production incident (2026-09-12): a Celery turn task hung forever with no
    error, no timeout, nothing — LLM_TIMEOUT_SECONDS was configured and IS
    plumbed into every provider client's own constructor, but nothing at the
    call sites independently enforced it, so a provider-SDK edge case that
    doesn't honour its own configured timeout had no backstop. This wrapper
    is that backstop, applied uniformly at every call site instead of
    trusting each provider's SDK to always get it right. Confirmed live by a
    second incident the same day (worker task 9c1a6ae2): the hang was in one
    of the *classifier* call sites below the reply draft — speaker
    detection, qualifier extraction, the consent/age gate, and booking
    detection all call the LLM too, and none of them were wrapped yet.

    ``timeout=None`` skips the bound entirely (an unmodified passthrough) —
    used by call sites that have no settings object handy (mainly tests);
    every production call site passes ``settings.llm_timeout_seconds``.

    A caught timeout always logs a WARNING here before raising ``LLMError`` —
    deliberately placed in this one shared wrapper rather than in each of the
    four (soon more?) call sites, so a timeout is visible in the worker logs
    even where the caller's own broad ``except Exception`` swallows the
    raised ``LLMError`` and falls back gracefully (speaker/extraction/
    consent-gate/booking are all documented "never fail the turn" — without
    this log line, that fallback is indistinguishable from a normal turn in
    the logs, and the next 9c1a6ae2-shaped incident would again look like
    nothing happened).
    """

    if timeout is None:
        return await client.complete(**kwargs)
    try:
        return await asyncio.wait_for(client.complete(**kwargs), timeout=timeout)
    except TimeoutError as exc:
        purpose = kwargs.get("purpose", "reply")
        logger.warning(
            "LLM call timed out: provider=%s purpose=%s timeout=%ss",
            client.provider,
            purpose,
            timeout,
            extra=log_extra(
                llm_provider=client.provider,
                llm_purpose=purpose,
                llm_timeout_seconds=timeout,
                llm_model=kwargs.get("model"),
            ),
        )
        raise LLMError(
            f"{client.provider} call timed out after {timeout}s (purpose={purpose})"
        ) from exc
