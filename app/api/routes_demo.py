"""Local WhatsApp-lookalike demo — NOT the production Meta webhook path.

Serves a static chat UI (see the sibling top-level ``demo/`` directory) and one
JSON endpoint that runs a message through the **real** conversation engine —
same guard, same KB, same LLM client the app built at startup (OpenRouter/
Vertex or whichever provider is configured) — so people can see the actual bot
work without a Meta/WhatsApp connection. The consent/age gate is skipped here
exactly like ``leadbot simulate`` skips it: this demonstrates the sales
conversation, not the gate.

Safety, on purpose:
- OFF by default (``DEMO_ENABLED=false``). This is a local-demo convenience,
  not something to expose on a deployed instance.
- Even when enabled, refuses to run unless ``WHATSAPP_CLIENT=fake`` — so a demo
  build can never place a real outbound WhatsApp send.
- Lives entirely in this module + ``demo/``; nothing here is imported by, or
  wired into, ``routes_webhook.py`` or anything else in the real Meta flow.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
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
from app.models.enums import (
    ConsentGate,
    LifecycleState,
    MessageDirection,
    MessageStatus,
    MessageType,
    SentBy,
)
from app.models.message import Message
from app.services import leads as leads_service
from app.services.conversation.engine import ConversationEngine
from app.services.phone import normalize_phone
from app.services.timeutils import utcnow
from app.services.windows import WindowService

logger = get_logger(__name__)

router = APIRouter(prefix="/demo", tags=["demo"])

# repo-root/demo — sibling to app/, deliberately outside the production package.
STATIC_DIR = Path(__file__).resolve().parents[2] / "demo"


class DemoChatRequest(BaseModel):
    phone: str = Field(
        ..., min_length=4, max_length=32,
        description="any-looking phone; a demo lead is created/reused per number",
    )
    message: str = Field(..., min_length=1, max_length=2000)


class DemoChatResponse(BaseModel):
    reply: str | None = None
    action: str
    skipped_reason: str | None = None
    booking_detected: bool = False


def _require_demo_enabled(settings: Settings) -> None:
    if not settings.demo_enabled:
        raise HTTPException(status_code=404, detail="demo UI is disabled (set DEMO_ENABLED=true)")
    if settings.whatsapp_client != "fake":
        # Belt-and-suspenders: never let a demo build place a real WhatsApp
        # send, even if someone enables DEMO_ENABLED on a live deployment.
        raise HTTPException(
            status_code=409,
            detail="demo UI refuses to run unless WHATSAPP_CLIENT=fake",
        )


@router.get("", include_in_schema=False)
async def demo_index(settings: Settings = Depends(get_app_settings)) -> FileResponse:
    _require_demo_enabled(settings)
    return FileResponse(STATIC_DIR / "index.html")


@router.post("/chat", response_model=DemoChatResponse)
async def demo_chat(
    payload: DemoChatRequest,
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    llm=Depends(get_llm_client),
    kb=Depends(get_knowledge_base),
    wa_client=Depends(get_wa_client),
) -> DemoChatResponse:
    """One turn through the real conversation engine, gate skipped.

    Mirrors ``leadbot simulate`` exactly: find-or-create the lead, force it
    engaged with the consent gate cleared, open its service window, persist
    the inbound message, then hand it to ``ConversationEngine.handle_inbound``
    — the same guard/regenerate/fallback loop, KB retrieval and LLM call as
    a real WhatsApp message would get.
    """

    _require_demo_enabled(settings)
    if llm is None or kb is None:
        raise HTTPException(
            status_code=503, detail="conversation engine not configured on this app instance"
        )

    norm = normalize_phone(payload.phone, settings.default_phone_region)
    lead, created = await leads_service.find_or_create(session, norm, source="demo")
    if created or lead.lifecycle_state == LifecycleState.NEVER_CONTACTED:
        lead.lifecycle_state = LifecycleState.ENGAGED
        lead.first_engaged_at = lead.first_engaged_at or utcnow()
    # Demo exercises the sales flow only — skip the consent+age gate, same as
    # `leadbot simulate` (test the gate itself via replay-webhook instead).
    if lead.consent_gate != ConsentGate.CLEARED:
        lead.consent_gate = ConsentGate.CLEARED

    await WindowService(redis, settings).touch(lead)

    inbound = Message(
        lead_id=lead.id,
        direction=MessageDirection.INBOUND,
        message_type=MessageType.TEXT,
        body=payload.message,
        status=MessageStatus.RECEIVED,
        sent_by=SentBy.LEAD,
        wa_message_id=f"wamid.DEMO-{uuid4()}",
        status_history=[],
    )
    session.add(inbound)
    await session.commit()

    engine = ConversationEngine(session, redis, settings, llm=llm, kb=kb, wa_client=wa_client)
    result = await engine.handle_inbound(lead, inbound)

    if result.action == "error":
        logger.warning("demo chat turn errored for a demo lead (trace=%s)", result.trace_id)

    return DemoChatResponse(
        reply=result.reply_text,
        action=result.action,
        skipped_reason=result.skipped_reason,
        booking_detected=result.booking_detected,
    )
