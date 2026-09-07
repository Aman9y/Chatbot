"""Lead lookup / find-or-create with race-safe dedup on phone_e164."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead import Lead
from app.services.phone import NormalizedPhone


async def get_by_phone(session: AsyncSession, e164: str) -> Lead | None:
    return await session.scalar(select(Lead).where(Lead.phone_e164 == e164))


async def find_or_create(
    session: AsyncSession,
    phone: NormalizedPhone,
    *,
    source: str | None = None,
    defaults: dict[str, Any] | None = None,
) -> tuple[Lead, bool]:
    """Return (lead, created). Concurrency-safe: a duplicate insert is caught via
    the unique constraint and the existing row is returned."""

    existing = await get_by_phone(session, phone.e164)
    if existing is not None:
        return existing, False

    lead = Lead(
        phone_e164=phone.e164,
        phone_raw=phone.raw,
        phone_country=phone.country,
        source=source,
        **(defaults or {}),
    )
    session.add(lead)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        session.expunge(lead)
        existing = await get_by_phone(session, phone.e164)
        if existing is None:  # pragma: no cover - would indicate a different constraint
            raise
        return existing, False
    return lead, True
