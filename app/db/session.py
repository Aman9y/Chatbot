"""Async engine / session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    connect_args: dict = {}
    kwargs: dict = {"pool_pre_ping": True, "future": True, "echo": False}
    if settings.database_url.startswith("sqlite"):
        # SQLite: no real pool, allow cross-thread use for the test harness.
        connect_args["check_same_thread"] = False
    return create_async_engine(settings.database_url, connect_args=connect_args, **kwargs)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, autoflush=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields a session, rolls back on unhandled error."""

    maker = get_sessionmaker()
    async with maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def reset_engine_cache() -> None:
    """Test helper: drop cached engine/sessionmaker after changing settings."""

    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
