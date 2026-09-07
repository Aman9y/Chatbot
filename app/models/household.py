from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.lead import Lead


class Household(UUIDMixin, TimestampMixin, Base):
    """A family unit: typically one student lead + one parent lead sharing a thread.

    See critique D3 — students and parents are separate phone numbers; linking
    them prevents double-messaging and lets tone logic know the other party
    exists.
    """

    __tablename__ = "households"

    external_ref: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    label: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)

    leads: Mapped[list[Lead]] = relationship(back_populates="household")
