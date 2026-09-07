from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.errors import (
    IllegalTransition,
    OptOutViolation,
    OutreachBlocked,
    PhoneNormalizationError,
    ServiceWindowClosed,
    WhatsAppAPIError,
)
from app.logging_config import get_logger, request_id_ctx

logger = get_logger("app.errors")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(PhoneNormalizationError)
    async def _phone(_: Request, exc: PhoneNormalizationError) -> JSONResponse:
        return JSONResponse(
            status_code=422, content={"error": "invalid_phone", "detail": str(exc)}
        )

    @app.exception_handler(OutreachBlocked)
    async def _blocked(_: Request, exc: OutreachBlocked) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"error": "outreach_blocked", "reasons": exc.decision.hard_blocks},
        )

    @app.exception_handler(OptOutViolation)
    async def _optout(_: Request, exc: OptOutViolation) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"error": "opt_out_violation", "detail": str(exc)}
        )

    @app.exception_handler(ServiceWindowClosed)
    async def _window(_: Request, exc: ServiceWindowClosed) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"error": "service_window_closed", "detail": str(exc)}
        )

    @app.exception_handler(IllegalTransition)
    async def _transition(_: Request, exc: IllegalTransition) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"error": "illegal_transition", "detail": str(exc)}
        )

    @app.exception_handler(WhatsAppAPIError)
    async def _wa(_: Request, exc: WhatsAppAPIError) -> JSONResponse:
        logger.error("whatsapp api error: %s", exc)
        return JSONResponse(
            status_code=502, content={"error": "whatsapp_api_error", "detail": str(exc)}
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error")
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "request_id": request_id_ctx.get()},
        )
