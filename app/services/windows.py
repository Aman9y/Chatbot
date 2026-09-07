"""Per-lead WhatsApp 24-hour customer-service window.

State lives in Redis (fast, TTL-managed) and is mirrored onto the lead row so
it survives a Redis flush and is queryable in SQL. The window resets on every
inbound message; a business-initiated template does NOT open it.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from redis.asyncio import Redis

from app.config import Settings
from app.models.lead import Lead
from app.services.timeutils import parse_iso, utcnow


class WindowService:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings

    @staticmethod
    def _key(lead_id: object) -> str:
        return f"wa:window:{lead_id}"

    async def touch(self, lead: Lead, *, now: datetime | None = None) -> datetime:
        now = now or utcnow()
        expiry = now + timedelta(hours=self._settings.service_window_hours)
        ttl = max(1, int((expiry - now).total_seconds()))
        await self._redis.set(self._key(lead.id), expiry.isoformat(), ex=ttl)
        lead.service_window_expires_at = expiry
        return expiry

    async def is_open(self, lead: Lead, *, now: datetime | None = None) -> bool:
        now = now or utcnow()
        raw = await self._redis.get(self._key(lead.id))
        if raw is not None:
            expiry = parse_iso(raw if isinstance(raw, str) else raw.decode())
            return bool(expiry and expiry > now)
        return bool(lead.service_window_expires_at and lead.service_window_expires_at > now)

    async def close(self, lead: Lead, *, now: datetime | None = None) -> None:
        now = now or utcnow()
        await self._redis.delete(self._key(lead.id))
        if lead.service_window_expires_at and lead.service_window_expires_at > now:
            lead.service_window_expires_at = now

    async def expires_at(self, lead: Lead) -> datetime | None:
        raw = await self._redis.get(self._key(lead.id))
        if raw is not None:
            return parse_iso(raw if isinstance(raw, str) else raw.decode())
        return lead.service_window_expires_at
