from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import TimestampMixin, UUIDMixin, enum_column
from app.models.enums import (
    ConsentStatus,
    EligibilityFlag,
    FunnelStage,
    InterestTemperature,
    LifecycleState,
    MinorPolicyStatus,
    MinorStatus,
    NeetCategory,
    RoleHint,
)

if TYPE_CHECKING:
    from app.models.consent import ConsentRecord
    from app.models.household import Household
    from app.models.lifecycle import LifecycleTransition
    from app.models.message import Message


_FUNNEL_MAP: dict[LifecycleState, FunnelStage] = {
    LifecycleState.NEVER_CONTACTED: FunnelStage.LEAD,
    LifecycleState.CONTACTED: FunnelStage.CONTACTED,
    LifecycleState.ENGAGED: FunnelStage.ENGAGED,
    LifecycleState.SILENT: FunnelStage.ENGAGED,
    LifecycleState.NURTURE: FunnelStage.ENGAGED,
    LifecycleState.DORMANT: FunnelStage.LOST,
    LifecycleState.HANDOFF: FunnelStage.HANDED_OFF,
    LifecycleState.OPTED_OUT: FunnelStage.LOST,
}


class Lead(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "leads"

    household_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("households.id", ondelete="SET NULL"), index=True
    )
    household: Mapped[Household | None] = relationship(back_populates="leads")

    # --- contact ---------------------------------------------------------
    phone_e164: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    phone_raw: Mapped[str | None] = mapped_column(String(64))
    phone_country: Mapped[str | None] = mapped_column(String(2))
    full_name: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(120))
    language_preference: Mapped[str | None] = mapped_column(String(32))
    source: Mapped[str | None] = mapped_column(String(120))
    role_hint: Mapped[RoleHint] = enum_column(
        RoleHint, default=RoleHint.UNKNOWN, nullable=False
    )

    # --- age / minor (critique A2, D1) ---------------------------------
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    age_years: Mapped[int | None] = mapped_column(Integer)
    is_minor: Mapped[MinorStatus] = enum_column(
        MinorStatus, default=MinorStatus.UNKNOWN, nullable=False, index=True
    )
    minor_policy_status: Mapped[MinorPolicyStatus] = enum_column(
        MinorPolicyStatus, default=MinorPolicyStatus.NOT_APPLICABLE, nullable=False
    )

    # --- eligibility --------------------------------------------------
    neet_score: Mapped[int | None] = mapped_column(Integer)
    neet_category: Mapped[NeetCategory] = enum_column(
        NeetCategory, default=NeetCategory.UNKNOWN, nullable=False
    )
    neet_year: Mapped[int | None] = mapped_column(Integer)
    eligibility_flag: Mapped[EligibilityFlag] = enum_column(
        EligibilityFlag, default=EligibilityFlag.UNKNOWN, nullable=False
    )

    # --- interest ------------------------------------------------------
    target_country: Mapped[str | None] = mapped_column(String(80))
    budget_band: Mapped[str | None] = mapped_column(String(60))
    intake_year: Mapped[int | None] = mapped_column(Integer)
    interest_temperature: Mapped[InterestTemperature] = enum_column(
        InterestTemperature, default=InterestTemperature.UNKNOWN, nullable=False
    )

    # --- consent (critique A1, D2) -----------------------------------
    consent_status: Mapped[ConsentStatus] = enum_column(
        ConsentStatus, default=ConsentStatus.UNKNOWN, nullable=False, index=True
    )
    consent_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    consent_last_source: Mapped[str | None] = mapped_column(String(255))
    consent_last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime)
    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime)

    # --- lifecycle / single state machine (critique A5) ---------------
    lifecycle_state: Mapped[LifecycleState] = enum_column(
        LifecycleState, default=LifecycleState.NEVER_CONTACTED, nullable=False, index=True
    )
    contacted_at: Mapped[datetime | None] = mapped_column(DateTime)
    first_engaged_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_outbound_at: Mapped[datetime | None] = mapped_column(DateTime)
    booked_at: Mapped[datetime | None] = mapped_column(DateTime)
    service_window_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    silent_retry_round: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- human handoff (critique B6, D5) ------------------------------
    human_owned: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    human_owned_since: Mapped[datetime | None] = mapped_column(DateTime)
    assigned_counselor: Mapped[str | None] = mapped_column(String(120))
    no_show: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    notes: Mapped[str | None] = mapped_column(Text)

    # --- relationships ----------------------------------------------
    messages: Mapped[list[Message]] = relationship(
        back_populates="lead",
        order_by="Message.created_at",
        cascade="all, delete-orphan",
    )
    consent_records: Mapped[list[ConsentRecord]] = relationship(
        back_populates="lead",
        order_by="ConsentRecord.created_at",
        cascade="all, delete-orphan",
    )
    transitions: Mapped[list[LifecycleTransition]] = relationship(
        back_populates="lead",
        order_by="LifecycleTransition.created_at",
        cascade="all, delete-orphan",
    )

    # --- derived (not persisted) -----------------------------------
    @property
    def funnel_stage(self) -> FunnelStage:
        """Derived from lifecycle_state. QUALIFIED is intentionally never
        returned here — the qualification predicate is a later-phase concern
        (critique B5)."""

        return _FUNNEL_MAP.get(self.lifecycle_state, FunnelStage.LEAD)

    def service_window_open(self, *, now: datetime | None = None) -> bool:
        from app.models.common import utcnow

        now = now or utcnow()
        return bool(self.service_window_expires_at and self.service_window_expires_at > now)

    def engagement_phase(self, *, now: datetime | None = None) -> str:
        """0-24h 'push', 24-48h 'handoff', 48h+ 'nurture' (plan §2 / critique A6).

        NOTE: measured from ``first_engaged_at`` and does NOT reset on later
        replies. The "reset on meaningful booking progress" refinement is still
        pending a definition from Hamza (critique A6) and is not implemented.
        """

        from app.config import get_settings
        from app.models.common import utcnow

        if self.lifecycle_state == LifecycleState.HANDOFF or self.booked_at is not None:
            return "n/a"
        if self.first_engaged_at is None:
            return "first_contact"
        settings = get_settings()
        now = now or utcnow()
        hours = (now - self.first_engaged_at).total_seconds() / 3600
        if hours < settings.engagement_push_hours:
            return "push"
        if hours < settings.engagement_handoff_hours:
            return "handoff"
        return "nurture"

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Lead {self.phone_e164} {self.lifecycle_state}>"
