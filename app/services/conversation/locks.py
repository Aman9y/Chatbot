"""Per-lead turn lock (build-plan §3.3).

A Redis lock keyed to the lead is held from just before the conversation engine
starts until the outbound send has completed. Any other inbound turn for the
same lead waits its turn rather than running a second LLM call on stale state.

The lock is advisory and TTL-bounded: if the holder dies, it frees itself after
``ttl_seconds`` so a lead is never permanently stuck.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from redis.asyncio import Redis

# Delete the key only if we still own it (token match) — avoids releasing a lock
# that already expired and was re-acquired by someone else.
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


@dataclass
class LockHandle:
    key: str
    token: str
    acquired: bool


class LeadTurnLock:
    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = max(1, ttl_seconds)

    @staticmethod
    def key(lead_id: object) -> str:
        return f"lead:turn:lock:{lead_id}"

    async def try_acquire(self, lead_id: object) -> LockHandle:
        key = self.key(lead_id)
        token = uuid.uuid4().hex
        ok = await self._redis.set(key, token, nx=True, ex=self._ttl)
        return LockHandle(key=key, token=token, acquired=bool(ok))

    async def release(self, handle: LockHandle) -> None:
        if not handle.acquired:
            return
        try:
            await self._redis.eval(_RELEASE_LUA, 1, handle.key, handle.token)
        except Exception:  # noqa: BLE001 - fall back to a best-effort delete
            current = await self._redis.get(handle.key)
            if current == handle.token or current == handle.token.encode():
                await self._redis.delete(handle.key)

    async def is_held(self, lead_id: object) -> bool:
        return bool(await self._redis.get(self.key(lead_id)))
