from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import TimestampMixin, UUIDMixin, enum_column
from app.models.enums import LifecycleEvent, LifecycleState

if TYPE_CHECKING:
    from app.models.lead import Lead


class LifecycleTransition(UUIDMixin, TimestampMixin, Base):
    """Append-only audit log of every state-machine event applied to a lead."""

    __tablename__ = "lifecycle_transitions"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    lead: Mapped[Lead] = relationship(back_populates="transitions")

    from_state: Mapped[LifecycleState] = enum_column(LifecycleState, nullable=False)
    to_state: Mapped[LifecycleState] = enum_column(LifecycleState, nullable=False)
    event: Mapped[LifecycleEvent] = enum_column(LifecycleEvent, nullable=False)

    actor: Mapped[str] = mapped_column(String(60), default="system", nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    context: Mapped[dict[str, Any] | None] = mapped_column(JSON)
