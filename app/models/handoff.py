from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import TimestampMixin, UUIDMixin, enum_column
from app.models.enums import HandoffTrigger


class HandoffNotification(UUIDMixin, TimestampMixin, Base):
    """A signal to the human counsellor that a lead needs them.

    Phase 3 delivery is a log line + optional webhook POST. The counsellor CRM
    integration is a later phase; this table is the durable queue in the meantime.
    """

    __tablename__ = "handoff_notifications"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    trigger: Mapped[HandoffTrigger] = enum_column(HandoffTrigger, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    delivered: Mapped[bool] = mapped_column(default=False, nullable=False, index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    delivery_channel: Mapped[str | None] = mapped_column(String(40))
    delivery_error: Mapped[str | None] = mapped_column(Text)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime)
