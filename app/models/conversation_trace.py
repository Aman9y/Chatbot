from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import TimestampMixin, UUIDMixin, enum_column
from app.models.enums import RoleHint


class ConversationTrace(UUIDMixin, TimestampMixin, Base):
    """One row per inbound message the conversation engine acted on.

    Full decision trail (critique C2): retrieved KB, prompt inputs, raw LLM
    drafts, guard verdicts, the final action. This is what you read when a lead
    later says "the bot told me X".
    """

    __tablename__ = "conversation_traces"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True, nullable=False
    )
    inbound_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), index=True
    )
    outbound_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )

    # decision inputs
    speaker_detected: Mapped[RoleHint] = enum_column(
        RoleHint, default=RoleHint.UNKNOWN, nullable=False
    )
    speaker_method: Mapped[str | None] = mapped_column(String(32))
    engagement_phase: Mapped[str | None] = mapped_column(String(24))
    lifecycle_state: Mapped[str | None] = mapped_column(String(24))
    kb_chunk_ids: Mapped[list[str] | None] = mapped_column(JSON)

    # LLM
    llm_provider: Mapped[str | None] = mapped_column(String(24))
    llm_model: Mapped[str | None] = mapped_column(String(64))
    llm_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    llm_output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    llm_latency_ms: Mapped[float | None] = mapped_column(Numeric(10, 2))
    llm_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # guard
    draft_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    drafts: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    guard_verdict: Mapped[str | None] = mapped_column(String(32))
    guard_violations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)

    # qualification + scoring (plan §3 / playbook Part 6)
    extracted_qualifiers: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    lead_score: Mapped[str | None] = mapped_column(String(16))
    lead_score_reason: Mapped[str | None] = mapped_column(String(255))
    interest_temperature: Mapped[str | None] = mapped_column(String(16))

    # outcome
    final_action: Mapped[str] = mapped_column(String(32), nullable=False, default="skipped")
    final_text: Mapped[str | None] = mapped_column(Text)
    skipped_reason: Mapped[str | None] = mapped_column(String(64))
    booking_detected: Mapped[bool] = mapped_column(default=False, nullable=False)
    booking_details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)

    cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    cost_currency: Mapped[str | None] = mapped_column(String(8))

    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
