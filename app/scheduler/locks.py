"""Redis advisory lock so overlapping beat ticks don't double-process a sweep."""

from __future__ import annotations

from redis.asyncio import Redis


class SweepLock:
    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    @staticmethod
    def _key(name: str) -> str:
        return f"sweep:lock:{name}"

    async def acquire(self, name: str) -> bool:
        return bool(await self._redis.set(self._key(name), "1", nx=True, ex=self._ttl))

    async def release(self, name: str) -> None:
        await self._redis.delete(self._key(name))
