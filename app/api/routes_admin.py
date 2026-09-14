from __future__ import annotations

import secrets
from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_db, get_redis
from app.config import Settings
from app.logging_config import get_logger
from app.models.consent import ConsentRecord
from app.models.conversation_trace import ConversationTrace
from app.models.handoff import HandoffNotification
from app.models.lead import Lead
from app.models.lifecycle import LifecycleTransition
from app.models.message import Message
from app.services.phone import normalize_phone

logger = get_logger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


def _verify_admin_secret(
    settings: Settings,
    x_api_secret: str | None,
) -> bool:
    if not x_api_secret:
        return False
    allowed = [s for s in [settings.webjs_api_secret, settings.meta_app_secret] if s]
    return any(secrets.compare_digest(x_api_secret, a) for a in allowed)


@router.post("/reset-lead")
@router.delete("/reset-lead")
@router.get("/reset-lead")
async def reset_lead(
    phone: str = Query(..., description="Phone number to wipe"),
    x_api_secret: str | None = Header(None, alias="X-API-Secret"),
    secret: str | None = Query(None, description="Secret fallback as query param"),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    redis = Depends(get_redis),
):
    auth_token = x_api_secret or secret
    if not _verify_admin_secret(settings, auth_token):
        raise HTTPException(status_code=401, detail="Unauthorized: invalid or missing X-API-Secret")

    try:
        norm = normalize_phone(phone)
        e164 = norm.e164
    except Exception:
        e164 = phone.strip()

    suffix = phone.strip()[-10:]

    # Find leads matching phone
    stmt = select(Lead).where(
        (Lead.phone_e164 == e164)
        | (Lead.phone_raw == phone)
        | (Lead.phone_e164.like(f"%{suffix}%"))
    )
    result = await db.execute(stmt)
    leads = result.scalars().all()
    actual_lead_ids = [l.id for l in leads]

    deleted_counts = {}
    if actual_lead_ids:
        # Delete child tables first
        for model, name in [
            (Message, "messages"),
            (LifecycleTransition, "lifecycle_transitions"),
            (ConsentRecord, "consent_records"),
            (HandoffNotification, "handoff_notifications"),
            (ConversationTrace, "conversation_traces"),
        ]:
            del_stmt = delete(model).where(model.lead_id.in_(actual_lead_ids))
            res = await db.execute(del_stmt)
            deleted_counts[name] = res.rowcount

        del_leads = delete(Lead).where(Lead.id.in_(actual_lead_ids))
        res_leads = await db.execute(del_leads)
        deleted_counts["leads"] = res_leads.rowcount
    else:
        deleted_counts["leads"] = 0

    # Also clean messages where counterparty or to_phone matches
    msg_del = delete(Message).where(
        (Message.counterparty_phone.like(f"%{suffix}%"))
        | (Message.to_phone.like(f"%{suffix}%"))
    )
    res_msg = await db.execute(msg_del)
    deleted_counts["orphan_messages"] = res_msg.rowcount

    await db.commit()

    # Flush Redis cache/turn lock for this phone if any
    if redis is not None:
        try:
            keys = await redis.keys(f"*{suffix}*")
            if keys:
                await redis.delete(*keys)
        except Exception as e:
            logger.warning("Redis cleanup failed (non-fatal): %s", e)

    return {
        "status": "success",
        "phone": e164,
        "deleted_lead_ids": [str(lid) for lid in actual_lead_ids],
        "deleted_counts": deleted_counts,
    }
