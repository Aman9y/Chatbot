from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import TimestampMixin, UUIDMixin


class WebhookEvent(UUIDMixin, TimestampMixin, Base):
    """One received Meta webhook POST.

    ``event_hash`` (sha256 of the raw body) is the deduplication key. Idempotency
    is keyed on ``processed`` rather than mere existence: a redelivery of an
    event that failed processing is retried, one that succeeded is a no-op
    (critique A3 / C4).
    """

    __tablename__ = "webhook_events"

    event_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)

    object_type: Mapped[str | None] = mapped_column(String(40))
    source_ip: Mapped[str | None] = mapped_column(String(64))

    raw_text: Mapped[str | None] = mapped_column(Text)
    raw_body: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    headers: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    entry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)
    processing_error: Mapped[str | None] = mapped_column(Text)
    processing_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
