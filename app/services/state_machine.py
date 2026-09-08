"""The single centralized lifecycle state machine (plan §5, critique A5).

`next_state` is a pure function. `apply_event` persists the change plus an audit
row and updates the derived timestamps on the lead.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import IllegalTransition
from app.models.enums import LifecycleEvent as E
from app.models.enums import LifecycleState as S
from app.models.lead import Lead
from app.models.lifecycle import LifecycleTransition
from app.services.timeutils import utcnow

# (from_state, event) -> to_state. OPT_OUT is handled specially (any -> OPTED_OUT).
_TABLE: dict[tuple[S, E], S] = {
    # Never contacted
    (S.NEVER_CONTACTED, E.OUTBOUND_TEMPLATE_SENT): S.CONTACTED,
    (S.NEVER_CONTACTED, E.INBOUND_MESSAGE): S.ENGAGED,  # unsolicited / click-to-WA
    # Contacted (template sent, awaiting first reply)
    (S.CONTACTED, E.INBOUND_MESSAGE): S.ENGAGED,
    (S.CONTACTED, E.OUTBOUND_TEMPLATE_SENT): S.CONTACTED,
    (S.CONTACTED, E.SERVICE_WINDOW_EXPIRED): S.SILENT,
    # Engaged (inside a 24h window, LLM-driven later)
    (S.ENGAGED, E.INBOUND_MESSAGE): S.ENGAGED,
    (S.ENGAGED, E.OUTBOUND_MESSAGE_SENT): S.ENGAGED,
    (S.ENGAGED, E.BOOKING_CONFIRMED): S.HANDOFF,
    (S.ENGAGED, E.HUMAN_TAKEOVER): S.HANDOFF,
    (S.ENGAGED, E.NURTURE_TIMEOUT): S.NURTURE,
    (S.ENGAGED, E.SERVICE_WINDOW_EXPIRED): S.SILENT,
    # Silent (window closed, needs a re-open template)
    (S.SILENT, E.OUTBOUND_TEMPLATE_SENT): S.SILENT,
    (S.SILENT, E.INBOUND_MESSAGE): S.ENGAGED,
    (S.SILENT, E.RETRY_ROUNDS_EXHAUSTED): S.DORMANT,
    # Nurture (slow cadence)
    (S.NURTURE, E.INBOUND_MESSAGE): S.ENGAGED,
    (S.NURTURE, E.OUTBOUND_TEMPLATE_SENT): S.NURTURE,
    (S.NURTURE, E.OUTBOUND_MESSAGE_SENT): S.NURTURE,
    (S.NURTURE, E.BOOKING_CONFIRMED): S.HANDOFF,
    (S.NURTURE, E.HUMAN_TAKEOVER): S.HANDOFF,
    (S.NURTURE, E.SERVICE_WINDOW_EXPIRED): S.SILENT,
    (S.NURTURE, E.RETRY_ROUNDS_EXHAUSTED): S.DORMANT,
    # Dormant (deprioritized)
    (S.DORMANT, E.INBOUND_MESSAGE): S.ENGAGED,
    (S.DORMANT, E.OUTBOUND_TEMPLATE_SENT): S.DORMANT,
    # Handoff (human owns the thread — bot does not restart, critique B6)
    (S.HANDOFF, E.INBOUND_MESSAGE): S.HANDOFF,
    (S.HANDOFF, E.BOOKING_CONFIRMED): S.HANDOFF,
    (S.HANDOFF, E.HUMAN_RELEASE): S.ENGAGED,
}


def next_state(current: S, event: E) -> S:
    if event == E.OPT_OUT:
        return S.OPTED_OUT
    if current == S.OPTED_OUT:
        # Opt-out is sticky. Re-opt-in is a deliberate, separate action, not an
        # automatic transition.
        raise IllegalTransition(current, event)
    try:
        return _TABLE[(current, event)]
    except KeyError as exc:
        raise IllegalTransition(current, event) from exc


def is_allowed(current: S, event: E) -> bool:
    try:
        next_state(current, event)
        return True
    except IllegalTransition:
        return False


async def apply_event(
    session: AsyncSession,
    lead: Lead,
    event: E,
    *,
    actor: str = "system",
    reason: str | None = None,
    context: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> LifecycleTransition:
    """Apply `event` to `lead`, persisting the new state, an audit row, and
    updating derived timestamps. Raises IllegalTransition for undefined pairs."""

    now = now or utcnow()
    from_state = lead.lifecycle_state
    to_state = next_state(from_state, event)

    lead.lifecycle_state = to_state

    if to_state == S.CONTACTED and lead.contacted_at is None:
        lead.contacted_at = now
    if to_state == S.ENGAGED and lead.first_engaged_at is None:
        lead.first_engaged_at = now
    if event == E.BOOKING_CONFIRMED:
        lead.booked_at = now
    if to_state == S.OPTED_OUT and lead.opted_out_at is None:
        lead.opted_out_at = now
    if to_state == S.DORMANT and lead.dormant_at is None:
        lead.dormant_at = now
    if event in (E.HUMAN_TAKEOVER, E.BOOKING_CONFIRMED) and not lead.human_owned:
        lead.human_owned = True
        lead.human_owned_since = now
    if event == E.HUMAN_RELEASE:
        lead.human_owned = False
        lead.human_owned_since = None

    # Scheduler bookkeeping: a fresh engagement clears re-engagement counters so
    # the cadence restarts from scratch next time they go quiet (Phase 5 + 6).
    if to_state == S.ENGAGED and from_state != S.ENGAGED:
        lead.nudge_count = 0
        lead.phase_handoff_notified = False
        lead.silent_retry_round = 0
        lead.nurture_round = 0
        lead.next_reengagement_at = None
        lead.dormant_at = None
        # a fresh engagement re-arms the hot-lead CTA (score is recomputed the
        # next turn anyway)
        lead.counsellor_cta_sent = False

    transition = LifecycleTransition(
        lead_id=lead.id,
        from_state=from_state,
        to_state=to_state,
        event=event,
        actor=actor,
        reason=reason,
        context=context,
    )
    session.add(transition)
    await session.flush()
    return transition
