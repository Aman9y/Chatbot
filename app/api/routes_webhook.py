from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_app_settings,
    get_db,
    get_knowledge_base,
    get_llm_client,
    get_redis,
    get_wa_client,
)
from app.config import Settings
from app.logging_config import get_logger
from app.services.webhook_processor import WebhookProcessor

logger = get_logger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])


@router.get("/whatsapp")
async def verify_webhook(
    request: Request,
    settings: Settings = Depends(get_app_settings),
) -> Response:
    """Meta webhook verification handshake."""

    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge", "")

    if mode == "subscribe" and token and token == settings.meta_verify_token:
        return PlainTextResponse(challenge)
    logger.warning("webhook verification failed (mode=%s)", mode)
    return PlainTextResponse("verification failed", status_code=403)


@router.post("/whatsapp")
async def receive_webhook(
    request: Request,
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    llm=Depends(get_llm_client),
    kb=Depends(get_knowledge_base),
    wa_client=Depends(get_wa_client),
) -> Response:
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    source_ip = request.client.host if request.client else None

    processor = WebhookProcessor(
        session, redis, settings, llm=llm, kb=kb, wa_client=wa_client
    )
    result = await processor.ingest(
        raw_body=raw_body,
        signature_header=signature,
        headers=dict(request.headers),
        source_ip=source_ip,
    )
    body: dict = {"status": result.status}
    if result.event_id:
        body["event_id"] = result.event_id
    if result.detail:
        body["detail"] = result.detail
    return JSONResponse(status_code=result.http_status, content=body)
