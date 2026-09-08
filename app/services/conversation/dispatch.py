"""Turn dispatch (build-plan §3).

Layer 1 — idempotency — lives in the webhook processor (wamid + payload-hash
dedup). Layers 2 and 3 live here:

  * **Debounce (§3.2):** after the webhook has acked Meta, wait
    ``turn_debounce_ms`` before processing so a lead's rapid-fire messages are
    merged into one turn (``ConversationEngine.handle_pending_turn``).
  * **Per-lead lock (§3.3):** a Redis lock keyed to the lead is held across the
    whole engine run + outbound send, so two turns for the same lead never run
    in parallel on stale state.

``run_lead_turn`` is the single async core. In ``inline`` dispatch the webhook
calls the engine directly (one message at a time, no lock/debounce — dev/tests);
in ``celery`` dispatch the webhook enqueues ``process_lead_turn`` which calls
this.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.logging_config import get_logger, log_extra
from app.models.lead import Lead
from app.services.conversation.engine import ConversationEngine, ConversationResult
from app.services.conversation.locks import LeadTurnLock
from app.services.knowledge.base import KnowledgeBase
from app.services.llm.base import LLMClient

logger = get_logger(__name__)


class LockUnavailable(RuntimeError):
    """The per-lead lock is held by another turn; the caller should retry."""

    def __init__(self, lead_id: object) -> None:
        super().__init__(f"turn lock held for lead {lead_id}")
        self.lead_id = lead_id


@dataclass
class TurnDeps:
    session: AsyncSession
    redis: Redis
    settings: Settings
    llm: LLMClient
    kb: KnowledgeBase
    wa_client: object


async def run_lead_turn(deps: TurnDeps, lead_id: object) -> ConversationResult:
    """Debounce, take the per-lead lock, process every pending inbound message as
    one turn, release the lock. Raises :class:`LockUnavailable` if the lock is
    held (Celery retries; the inline caller ignores it — the current holder will
    pick up the pending messages)."""

    s = deps.settings
    lock = LeadTurnLock(deps.redis, s.turn_lock_ttl_seconds)
    handle = await lock.try_acquire(lead_id)
    if not handle.acquired:
        raise LockUnavailable(lead_id)

    try:
        if s.turn_debounce_ms > 0:
            await asyncio.sleep(s.turn_debounce_ms / 1000)

        lead = await deps.session.get(Lead, lead_id)
        if lead is None:  # pragma: no cover - defensive
            return ConversationResult("skipped", skipped_reason="lead_gone")

        engine = ConversationEngine(
            deps.session,
            deps.redis,
            s,
            llm=deps.llm,
            kb=deps.kb,
            wa_client=deps.wa_client,
        )
        result = await engine.handle_pending_turn(lead)
        logger.info(
            "turn processed",
            extra=log_extra(lead=str(lead_id), action=result.action),
        )
        return result
    finally:
        await lock.release(handle)
