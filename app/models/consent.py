from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import TimestampMixin, UUIDMixin, enum_column
from app.models.enums import ConsentMethod, ConsentStatus

if TYPE_CHECKING:
    from app.models.lead import Lead


class ConsentRecord(UUIDMixin, TimestampMixin, Base):
    """Append-only consent audit trail (critique A1 / D2).

    Each row is one consent event. ``consent_text`` holds the *exact wording*
    the person saw at capture — for the legacy CSV import this is NULL and
    ``verified`` is False, which is the honest representation of "we assert they
    opted in but haven't confirmed the wording."
    """

    __tablename__ = "consent_records"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    lead: Mapped[Lead] = relationship(back_populates="consent_records")

    channel: Mapped[str] = mapped_column(String(20), default="whatsapp", nullable=False)
    status: Mapped[ConsentStatus] = enum_column(ConsentStatus, nullable=False)
    method: Mapped[ConsentMethod] = enum_column(ConsentMethod, nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    consent_text: Mapped[str | None] = mapped_column(Text)
    source_reference: Mapped[str | None] = mapped_column(String(255))
    evidence_locator: Mapped[str | None] = mapped_column(String(255))
    captured_at: Mapped[datetime | None] = mapped_column(DateTime)

    actor: Mapped[str] = mapped_column(String(40), default="system", nullable=False)
    inbound_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)
