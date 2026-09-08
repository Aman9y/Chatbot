from __future__ import annotations

from contextlib import asynccontextmanager

import redis.asyncio as redis_asyncio
from fastapi import FastAPI

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.middleware import RequestContextMiddleware
from app.api.routes_health import router as health_router
from app.api.routes_webhook import router as webhook_router
from app.config import get_settings
from app.logging_config import configure_logging, get_logger
from app.services.knowledge.yaml_kb import load_knowledge_base
from app.services.llm.factory import build_llm_client
from app.services.whatsapp.factory import build_whatsapp_client

logger = get_logger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    app.state.settings = settings
    app.state.redis = redis_asyncio.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_keepalive=True,
        health_check_interval=30,
        retry_on_timeout=True,
    )
    app.state.wa_client = build_whatsapp_client(settings)
    app.state.llm_client = build_llm_client(settings)
    app.state.knowledge_base = load_knowledge_base(settings.kb_path, strict=True)

    unresolved = settings.unresolved_phase1_items
    if unresolved:
        logger.warning(
            "starting with %d unresolved Phase-1 items: %s",
            len(unresolved),
            "; ".join(unresolved),
        )
    logger.info(
        "startup complete (whatsapp_client=%s, llm_provider=%s, kb_chunks=%d)",
        app.state.wa_client.name,
        app.state.llm_client.provider,
        app.state.knowledge_base.size,
    )
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await app.state.wa_client.aclose()
        await app.state.llm_client.aclose()
        logger.info("shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="MBBS Abroad Lead Bot",
        version=__version__,
        summary="Phase 2 — ingestion pipe (webhook, persistence, state machine, outreach guard)",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(webhook_router)
    return app


app = create_app()
