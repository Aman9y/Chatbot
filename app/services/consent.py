"""Consent + minor-status logic (critique A1, A2, D2)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.consent import ConsentRecord
from app.models.enums import (
    ConsentMethod,
    ConsentStatus,
    LifecycleEvent,
    LifecycleState,
    MinorPolicyStatus,
    MinorStatus,
)
from app.models.lead import Lead
from app.models.message import Message
from app.services import state_machine
from app.services.timeutils import utcnow


def years_between(born: date, today: date) -> int:
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def evaluate_minor(
    dob: date | None,
    age: int | None,
    *,
    today: date | None = None,
) -> tuple[MinorStatus, int | None]:
    today = today or utcnow().date()
    if dob is not None:
        age = years_between(dob, today)
    if age is None:
        return MinorStatus.UNKNOWN, None
    return (MinorStatus.YES if age < 18 else MinorStatus.NO), age


def apply_minor_policy(lead: Lead, settings: Settings) -> None:
    """Set minor_policy_status from is_minor + the (still-unresolved) default policy."""

    if lead.is_minor == MinorStatus.YES:
        if lead.minor_policy_status in (
            MinorPolicyStatus.NOT_APPLICABLE,
            MinorPolicyStatus.PENDING_REVIEW,
        ):
            lead.minor_policy_status = settings.minor_default_policy
    elif lead.is_minor == MinorStatus.NO:
        lead.minor_policy_status = MinorPolicyStatus.NOT_APPLICABLE
    # UNKNOWN: leave as-is (NOT_APPLICABLE by default) — we cannot assert a policy.


async def record_consent(
    session: AsyncSession,
    lead: Lead,
    *,
    status: ConsentStatus,
    method: ConsentMethod,
    verified: bool = False,
    consent_text: str | None = None,
    source_reference: str | None = None,
    evidence_locator: str | None = None,
    captured_at: datetime | None = None,
    actor: str = "system",
    inbound_message: Message | None = None,
    notes: str | None = None,
) -> ConsentRecord:
    now = utcnow()
    record = ConsentRecord(
        lead_id=lead.id,
        status=status,
        method=method,
        verified=verified,
        consent_text=consent_text,
        source_reference=source_reference,
        evidence_locator=evidence_locator,
        captured_at=captured_at,
        actor=actor,
        inbound_message_id=inbound_message.id if inbound_message else None,
        notes=notes,
    )
    session.add(record)

    lead.consent_status = status
    lead.consent_last_source = source_reference or method.value
    lead.consent_last_evaluated_at = now
    if status == ConsentStatus.OPTED_IN:
        lead.consent_verified = verified
    elif status in (ConsentStatus.OPTED_OUT, ConsentStatus.WITHDRAWN):
        lead.consent_verified = False
        lead.opted_out_at = now

    await session.flush()
    return record


async def opt_out(
    session: AsyncSession,
    lead: Lead,
    *,
    reason_text: str | None,
    method: ConsentMethod = ConsentMethod.INBOUND_STOP_KEYWORD,
    inbound_message: Message | None = None,
    actor: str = "lead",
) -> ConsentRecord:
    """Full opt-out: audit record + sticky OPTED_OUT state transition."""

    record = await record_consent(
        session,
        lead,
        status=ConsentStatus.OPTED_OUT,
        method=method,
        verified=False,
        consent_text=(reason_text or "")[:1000] or None,
        source_reference=method.value,
        actor=actor,
        inbound_message=inbound_message,
        notes="Opt-out short-circuit (critique A3).",
    )
    if lead.lifecycle_state != LifecycleState.OPTED_OUT:
        await state_machine.apply_event(
            session,
            lead,
            LifecycleEvent.OPT_OUT,
            actor=actor,
            reason="inbound opt-out keyword",
        )
    return record
