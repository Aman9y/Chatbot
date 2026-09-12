from __future__ import annotations

import base64
import secrets

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


def _basic_auth_ok(settings: Settings, request: Request) -> bool:
    """Optional extra layer for a webhook source that can't sign requests the
    way Meta does (e.g. 360dialog) — HTTP Basic Auth embedded in the webhook
    URL itself. Blank config (default) = nothing to check = always ok, so
    this is a strict no-op unless WEBHOOK_BASIC_AUTH_USERNAME/PASSWORD are
    both set."""

    user = settings.webhook_basic_auth_username
    pw = settings.webhook_basic_auth_password
    if not user and not pw:
        return True
    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
    except Exception:  # noqa: BLE001 - malformed header -> just reject
        return False
    got_user, _, got_pw = decoded.partition(":")
    return secrets.compare_digest(got_user, user) and secrets.compare_digest(got_pw, pw)


@router.get("/whatsapp")
async def verify_webhook(
    request: Request,
    settings: Settings = Depends(get_app_settings),
) -> Response:
    """Meta webhook verification handshake."""

    if not _basic_auth_ok(settings, request):
        return PlainTextResponse("unauthorized", status_code=401)

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
    if not _basic_auth_ok(settings, request):
        return JSONResponse(status_code=401, content={"status": "unauthorized"})

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
