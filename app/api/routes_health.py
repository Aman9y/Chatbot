from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, Response
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.api.deps import get_app_settings, get_db, get_redis
from app.config import Settings

router = APIRouter(tags=["health"])


@router.get("/")
async def root(settings: Settings = Depends(get_app_settings)) -> dict:
    return {
        "service": "mbbs-abroad-lead-bot",
        "version": __version__,
        "phase": 2,
        "env": settings.app_env,
        "whatsapp_client": settings.whatsapp_client,
    }


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(
    response: Response,
    session: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict:
    checks: dict[str, str] = {}
    ok = True

    async def _probe(name: str, fn: Callable[[], Awaitable[object]]) -> None:
        nonlocal ok
        # One retry: absorbs the cold-connection blip the Docker port proxy
        # sometimes causes on the first request, without masking a real outage.
        for attempt in (1, 2):
            try:
                result = await fn()
                if result is False:
                    raise RuntimeError("probe returned false")
                checks[name] = "ok"
                return
            except Exception as exc:  # noqa: BLE001
                if attempt == 2:
                    checks[name] = f"error: {exc}"
                    ok = False
                else:
                    await asyncio.sleep(0.1)

    await _probe("database", lambda: session.execute(text("SELECT 1")))
    await _probe("redis", redis.ping)

    if not ok:
        response.status_code = 503
    return {"status": "ready" if ok else "not_ready", "checks": checks}
