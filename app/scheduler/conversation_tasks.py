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
from app.logging_config import configure_logging, get_logger, log_extra
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
    """Every path out of this task logs one unambiguous final line — sent,
    blocked/fallback, skipped, or failed — so a Celery worker log is never
    silent on what happened to a lead's turn. Before this, an exception
    anywhere in ``_run`` (building the WhatsApp/LLM client, the DB session,
    the engine itself) propagated out of the task with nothing beyond
    Celery's own "received" line to show for it in practice — this makes
    that outcome impossible: every branch below logs before returning or
    re-raising.
    """

    settings = get_settings()
    logger.info("process_lead_turn started", extra=log_extra(lead=lead_id))
    try:
        result = asyncio.run(_run(lead_id))
    except LockUnavailable as exc:
        logger.info(
            "process_lead_turn: lock busy, retrying",
            extra=log_extra(lead=lead_id),
        )
        raise self.retry(
            exc=exc,
            countdown=settings.turn_lock_retry_seconds,
            max_retries=settings.turn_lock_max_retries,
        ) from exc
    except Exception as exc:
        # The catch-all: whatever broke (missing WhatsApp client env var,
        # DB/Redis unreachable, an LLM call, the outbound send, anything
        # else) is logged here with the full traceback and the lead id,
        # then re-raised so Celery's own retry/failure bookkeeping (task
        # state, max_retries semantics elsewhere) still applies unchanged.
        logger.error(
            "process_lead_turn FAILED: %s: %s",
            type(exc).__name__,
            exc,
            extra=log_extra(lead=lead_id, error_type=type(exc).__name__),
            exc_info=True,
        )
        raise

    logger.info(
        "process_lead_turn finished: action=%s skipped_reason=%s booking=%s",
        result.get("action"),
        result.get("skipped_reason"),
        result.get("booking"),
        extra=log_extra(
            lead=lead_id,
            action=result.get("action"),
            skipped_reason=result.get("skipped_reason"),
        ),
    )
    return result
