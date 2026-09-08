"""Celery task that processes one lead's conversation turn (build-plan §3).

The webhook acks Meta immediately and enqueues ``process_lead_turn``; this task
applies the debounce window + per-lead lock and runs the conversation engine. If
the lock is held by another turn it retries with a countdown ("waits its turn").
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import redis.asyncio as redis_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.logging_config import configure_logging, get_logger
from app.scheduler.celery_app import celery_app
from app.services.conversation.dispatch import LockUnavailable, TurnDeps, run_lead_turn
from app.services.knowledge.yaml_kb import load_knowledge_base
from app.services.llm.factory import build_llm_client
from app.services.whatsapp.factory import build_whatsapp_client

logger = get_logger(__name__)


@asynccontextmanager
async def _turn_deps():
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args=(
            {"check_same_thread": False}
            if settings.database_url.startswith("sqlite")
            else {}
        ),
    )
    redis = redis_asyncio.from_url(settings.redis_url, decode_responses=True)
    wa_client = build_whatsapp_client(settings)
    llm = build_llm_client(settings)
    kb = load_knowledge_base(settings.kb_path, strict=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        async with session_factory() as session:
            yield TurnDeps(
                session=session,
                redis=redis,
                settings=settings,
                llm=llm,
                kb=kb,
                wa_client=wa_client,
            )
    finally:
        await redis.aclose()
        await wa_client.aclose()
        await llm.aclose()
        await engine.dispose()


async def _run(lead_id: str) -> dict:
    async with _turn_deps() as deps:
        result = await run_lead_turn(deps, lead_id)
        return {
            "lead_id": lead_id,
            "action": result.action,
            "skipped_reason": result.skipped_reason,
            "booking": result.booking_detected,
        }


@celery_app.task(
    name="app.scheduler.conversation_tasks.process_lead_turn",
    bind=True,
    acks_late=True,
    max_retries=None,
)
def process_lead_turn(self, lead_id: str) -> dict:
    settings = get_settings()
    try:
        return asyncio.run(_run(lead_id))
    except LockUnavailable as exc:
        logger.info("turn lock busy for lead %s; retrying", lead_id)
        raise self.retry(
            exc=exc,
            countdown=settings.turn_lock_retry_seconds,
            max_retries=settings.turn_lock_max_retries,
        ) from exc
