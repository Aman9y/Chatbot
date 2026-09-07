"""Structured logging setup + PII-aware helpers."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from typing import Any

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

_CONFIGURED = False


def mask_phone(phone: str | None) -> str:
    """Return a log-safe rendering of a phone number (keep last 4 digits)."""
    if not phone:
        return "<none>"
    digits = [c for c in phone if c.isdigit()]
    if len(digits) <= 4:
        return "***"
    return "***" + "".join(digits[-4:])


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id_ctx.get()
        if rid:
            payload["request_id"] = rid
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        return json.dumps(payload, default=str, ensure_ascii=False)


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "extra_fields"):
            record.extra_fields = {}
        return True


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    global _CONFIGURED
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_ContextFilter())
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s :: %(message)s")
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).handlers.clear()
        logging.getLogger(noisy).propagate = True

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name)


def log_extra(**fields: Any) -> dict[str, Any]:
    """Helper: `logger.info("msg", extra=log_extra(lead="x"))`."""
    return {"extra_fields": fields}
