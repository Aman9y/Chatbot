"""Bridge between sync Celery tasks and the async sweep implementations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

import redis.asyncio as redis_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.logging_config import configure_logging, get_logger
from app.scheduler.locks import SweepLock
from app.scheduler.sweeps import SweepDeps, SweepResult
from app.services.whatsapp.factory import build_whatsapp_client

logger = get_logger(__name__)


@asynccontextmanager
async def sweep_deps():
    """Fresh engine/redis/wa-client per sweep run — Celery tasks are sync and each
    invocation gets its own asyncio loop, so nothing is reused across runs."""

    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args=(
            {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
        ),
    )
    redis = redis_asyncio.from_url(settings.redis_url, decode_responses=True)
    wa_client = build_whatsapp_client(settings)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        async with session_factory() as session:
            yield SweepDeps(
                session=session, redis=redis, settings=settings, wa_client=wa_client
            )
    finally:
        await redis.aclose()
        await wa_client.aclose()
        await engine.dispose()


async def _run_one(
    name: str, fn: Callable[[SweepDeps], Awaitable[SweepResult]]
) -> dict:
    async with sweep_deps() as deps:
        lock = SweepLock(deps.redis, deps.settings.scheduler_lock_ttl_seconds)
        if not await lock.acquire(name):
            logger.info("sweep %s skipped: lock held", name)
            return {"sweep": name, "status": "locked"}
        try:
            result = await fn(deps)
            return {"status": "ok", **result.as_dict()}
        finally:
            await lock.release(name)


def run_sweep(name: str, fn: Callable[[SweepDeps], Awaitable[SweepResult]]) -> dict:
    return asyncio.run(_run_one(name, fn))


async def _run_all() -> list[dict]:
    from app.scheduler.sweeps import ALL_SWEEPS

    out: list[dict] = []
    async with sweep_deps() as deps:
        for name, fn in ALL_SWEEPS.items():
            try:
                result = await fn(deps)
                out.append({"status": "ok", **result.as_dict()})
            except Exception as exc:  # noqa: BLE001
                logger.exception("sweep %s crashed", name)
                out.append({"sweep": name, "status": "error", "error": str(exc)})
    return out


def run_all_sweeps_once() -> list[dict]:
    return asyncio.run(_run_all())
