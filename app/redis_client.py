"""Shared Redis client construction.

Production incident (2026-09-12): a Celery turn task hung indefinitely with
no error — traced to Redis clients built with no socket timeout at all
(redis-py's own default is ``socket_timeout=None``, i.e. wait forever). Every
place this app builds a Redis client should go through here so that gap
can't reopen at a new call site.
"""

from __future__ import annotations

import redis.asyncio as redis_asyncio
from redis.asyncio import Redis

from app.config import Settings


def build_redis_client(settings: Settings, *, decode_responses: bool = True) -> Redis:
    return redis_asyncio.from_url(
        settings.redis_url,
        decode_responses=decode_responses,
        socket_connect_timeout=settings.redis_connect_timeout_seconds,
        socket_timeout=settings.redis_socket_timeout_seconds,
        socket_keepalive=True,
        health_check_interval=30,
        retry_on_timeout=True,
    )
