from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.logging_config import get_logger, log_extra, request_id_ctx

logger = get_logger("app.request")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, logs one structured line per request."""

    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_ctx.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
            logger.info(
                "%s %s -> %s (%sms)",
                request.method,
                request.url.path,
                status,
                elapsed_ms,
                extra=log_extra(
                    method=request.method,
                    path=request.url.path,
                    status=status,
                    duration_ms=elapsed_ms,
                ),
            )
            request_id_ctx.reset(token)
