from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import get_session
from app.services.whatsapp.base import WhatsAppClient


async def get_db() -> AsyncIterator[AsyncSession]:
    async for session in get_session():
        yield session


def get_app_settings() -> Settings:
    return get_settings()


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_wa_client(request: Request) -> WhatsAppClient:
    return request.app.state.wa_client


def get_llm_client(request: Request):
    # None when the conversation engine is not wired (keeps webhook ingestion
    # working without an LLM — the processor skips the engine).
    return getattr(request.app.state, "llm_client", None)


def get_knowledge_base(request: Request):
    return getattr(request.app.state, "knowledge_base", None)
