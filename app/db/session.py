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

from app.config import Settings, get_settings


def engine_connect_args(settings: Settings) -> dict:
    """asyncpg has no default per-query timeout at all (only a 60s default on
    the initial connection) — a stuck query/connection would otherwise hang
    forever. Bounded here for every engine this app builds, whether the
    long-lived cached one below or the fresh-per-task ones Celery needs (see
    app/scheduler/conversation_tasks.py, app/scheduler/runner.py)."""

    if settings.database_url.startswith("sqlite"):
        # SQLite: no real pool, allow cross-thread use for the test harness.
        return {"check_same_thread": False}
    return {
        "timeout": settings.db_connect_timeout_seconds,
        "command_timeout": settings.db_command_timeout_seconds,
    }


def build_async_engine(settings: Settings) -> AsyncEngine:
    """A fresh, non-cached engine — for Celery tasks, which each get their own
    asyncio event loop (asyncpg connections can't be shared across loops)."""

    return create_async_engine(
        settings.database_url,
        connect_args=engine_connect_args(settings),
        pool_pre_ping=True,
        future=True,
        echo=False,
    )


@lru_cache
def get_engine() -> AsyncEngine:
    return build_async_engine(get_settings())


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
