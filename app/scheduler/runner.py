"""Bridge between sync Celery tasks and the async sweep implementations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db.session import build_async_engine
from app.logging_config import configure_logging, get_logger
from app.redis_client import build_redis_client
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

    engine = build_async_engine(settings)
    redis = build_redis_client(settings)
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
