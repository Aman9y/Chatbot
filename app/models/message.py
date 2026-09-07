from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import TimestampMixin, UUIDMixin, enum_column
from app.models.enums import (
    MessageDirection,
    MessageStatus,
    MessageType,
    SentBy,
    TemplateCategory,
)

if TYPE_CHECKING:
    from app.models.lead import Lead


class Message(UUIDMixin, TimestampMixin, Base):
    """Every inbound and outbound WhatsApp message, with full status history."""

    __tablename__ = "messages"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    lead: Mapped[Lead] = relationship(back_populates="messages")

    direction: Mapped[MessageDirection] = enum_column(
        MessageDirection, nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(20), default="whatsapp", nullable=False)

    # Meta identifiers. ``wa_message_id`` (wamid) is globally unique and is the
    # idempotency key for inbound messages and status updates.
    wa_message_id: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    wa_conversation_id: Mapped[str | None] = mapped_column(String(128), index=True)

    counterparty_phone: Mapped[str | None] = mapped_column(String(20), index=True)
    from_phone: Mapped[str | None] = mapped_column(String(20))
    to_phone: Mapped[str | None] = mapped_column(String(20))

    message_type: Mapped[MessageType] = enum_column(
        MessageType, default=MessageType.UNKNOWN, nullable=False
    )
    body: Mapped[str | None] = mapped_column(Text)

    template_name: Mapped[str | None] = mapped_column(String(128))
    template_language: Mapped[str | None] = mapped_column(String(16))
    template_category: Mapped[TemplateCategory | None] = enum_column(TemplateCategory)
    template_variables: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    status: Mapped[MessageStatus] = enum_column(
        MessageStatus, default=MessageStatus.QUEUED, nullable=False, index=True
    )
    status_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    # list of {"status": str, "at": iso8601, "source": str}
    status_history: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )

    error_code: Mapped[str | None] = mapped_column(String(40))
    error_title: Mapped[str | None] = mapped_column(String(255))
    error_detail: Mapped[str | None] = mapped_column(Text)
    error_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    # --- per-message cost (critique D4) --------------------------------
    pricing_model: Mapped[str | None] = mapped_column(String(40))
    pricing_category: Mapped[str | None] = mapped_column(String(40))
    pricing_type: Mapped[str | None] = mapped_column(String(40))
    billable: Mapped[bool | None] = mapped_column(Boolean)
    cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 5))
    cost_currency: Mapped[str | None] = mapped_column(String(8))

    sent_by: Mapped[SentBy] = enum_column(SentBy, default=SentBy.SYSTEM, nullable=False)

    # Meta's own timestamp for the message / status event (naive UTC).
    wa_timestamp: Mapped[datetime | None] = mapped_column(DateTime, index=True)

    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    webhook_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("webhook_events.id", ondelete="SET NULL")
    )
    # client-side idempotency for outbound sends (prevents double send on retry)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), unique=True, index=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Message {self.direction} {self.message_type} {self.status}>"
