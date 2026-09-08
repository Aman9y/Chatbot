"""Async implementations of the periodic scheduler sweeps (plan §5 / Phase 6).

Each sweep is a plain async function taking `SweepDeps`, so tests call them
directly with a test session. The Celery layer (tasks.py) only wraps them.

  send_consent_asks     drip the opt-in ask to un-gated leads (build-plan §2)
  expire_windows        24h window closed -> SILENT, schedule first re-open
  advance_engagement    time-based phase escalation the reactive engine misses
  send_in_window_nudges gentle "still there?" in the 0-24h push phase
  run_reengagement       SILENT/NURTURE re-open template rounds -> DORMANT at cap
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.logging_config import get_logger, log_extra, mask_phone
from app.models.enums import (
    ConsentGate,
    ConsentStatus,
    HandoffTrigger,
    LifecycleEvent,
    LifecycleState,
    SentBy,
    TemplateCategory,
)
from app.models.lead import Lead
from app.services import state_machine
from app.services.handoff import notify_counselor
from app.services.nudges import nudge_text
from app.services.outreach import OutreachService
from app.services.timeutils import utcnow
from app.services.whatsapp.base import WhatsAppClient
from app.services.windows import WindowService

logger = get_logger(__name__)


@dataclass
class SweepDeps:
    session: AsyncSession
    redis: Redis
    settings: Settings
    wa_client: WhatsAppClient


@dataclass
class SweepResult:
    sweep: str
    scanned: int = 0
    acted: int = 0
    skipped: int = 0
    errors: int = 0
    details: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "sweep": self.sweep,
            "scanned": self.scanned,
            "acted": self.acted,
            "skipped": self.skipped,
            "errors": self.errors,
        }


def _outreach(deps: SweepDeps) -> OutreachService:
    return OutreachService(deps.session, deps.redis, deps.settings, deps.wa_client)


# ---------------------------------------------------------------------------
async def send_consent_asks(deps: SweepDeps) -> SweepResult:
    """Drip the in-chat opt-in ask to leads that have not passed the gate
    (build-plan §2). OFF unless CONSENT_ASK_SWEEP_ENABLED — sending starts real
    contact with real people and needs an approved template + a deliberate call.
    """

    s = deps.settings
    result = SweepResult("send_consent_asks")
    if not (s.consent_gate_enabled and s.consent_ask_sweep_enabled):
        return result

    now = utcnow()
    gap = timedelta(hours=s.consent_ask_gap_hours)
    rows = (
        await deps.session.scalars(
            select(Lead)
            .where(Lead.consent_gate.in_([ConsentGate.PENDING_OPT_IN, ConsentGate.PENDING_AGE]))
            .where(
                Lead.lifecycle_state.in_(
                    [
                        LifecycleState.NEVER_CONTACTED,
                        LifecycleState.CONTACTED,
                        LifecycleState.SILENT,
                    ]
                )
            )
            .where(Lead.consent_status != ConsentStatus.OPTED_OUT)
            .where(Lead.human_owned.is_(False))
            .where(
                (Lead.consent_ask_sent_at.is_(None))
                | (Lead.consent_ask_sent_at <= now - gap)
            )
            .order_by(Lead.consent_ask_sent_at.is_(None).desc(), Lead.created_at)
            .limit(s.consent_asks_per_sweep)
        )
    ).all()

    outreach = _outreach(deps)
    for lead in rows:
        result.scanned += 1
        try:
            if lead.consent_ask_count >= s.consent_ask_max_rounds:
                if state_machine.is_allowed(
                    lead.lifecycle_state, LifecycleEvent.RETRY_ROUNDS_EXHAUSTED
                ):
                    await state_machine.apply_event(
                        deps.session,
                        lead,
                        LifecycleEvent.RETRY_ROUNDS_EXHAUSTED,
                        actor="scheduler",
                        reason=f"{lead.consent_ask_count} opt-in asks, no reply",
                        now=now,
                    )
                    result.acted += 1
                else:
                    result.skipped += 1
                continue

            decision = outreach.evaluate(lead, purpose="consent_ask")
            if not decision.allowed:
                result.skipped += 1
                continue

            await outreach.send_template(
                lead,
                template_name=s.consent_ask_template_name,
                language=s.consent_ask_template_language,
                category=_template_category(s.consent_ask_template_category),
                actor=SentBy.BOT,
                reason=f"opt-in ask round {lead.consent_ask_count + 1}",
                idempotency_key=f"consent_ask:{lead.id}:r{lead.consent_ask_count}",
                purpose="consent_ask",
                commit=False,
            )
            lead.consent_ask_sent_at = now
            lead.consent_ask_count += 1
            result.acted += 1
        except Exception:  # noqa: BLE001
            logger.exception("send_consent_asks failed for lead %s", lead.id)
            result.errors += 1

    await deps.session.commit()
    logger.info("sweep send_consent_asks: %s", result.as_dict())
    return result


# ---------------------------------------------------------------------------
async def expire_windows(deps: SweepDeps) -> SweepResult:
    s = deps.settings
    now = utcnow()
    result = SweepResult("expire_windows")
    windows = WindowService(deps.redis, s)

    rows = (
        await deps.session.scalars(
            select(Lead)
            .where(
                Lead.lifecycle_state.in_(
                    [LifecycleState.CONTACTED, LifecycleState.ENGAGED, LifecycleState.NURTURE]
                )
            )
            .where(Lead.service_window_expires_at.is_not(None))
            .where(Lead.service_window_expires_at <= now)
            .limit(s.scheduler_batch_size)
        )
    ).all()

    for lead in rows:
        result.scanned += 1
        try:
            await windows.close(lead, now=now)
            await state_machine.apply_event(
                deps.session,
                lead,
                LifecycleEvent.SERVICE_WINDOW_EXPIRED,
                actor="scheduler",
                reason="24h service window closed",
                now=now,
            )
            delay = s.reengagement_delay_hours(lead.silent_retry_round)
            lead.next_reengagement_at = now + timedelta(hours=delay)
            result.acted += 1
        except Exception:  # noqa: BLE001
            logger.exception("expire_windows failed for lead %s", lead.id)
            result.errors += 1

    await deps.session.commit()
    logger.info("sweep expire_windows: %s", result.as_dict())
    return result


# ---------------------------------------------------------------------------
async def advance_engagement(deps: SweepDeps) -> SweepResult:
    s = deps.settings
    now = utcnow()
    result = SweepResult("advance_engagement")

    rows = (
        await deps.session.scalars(
            select(Lead)
            .where(Lead.lifecycle_state == LifecycleState.ENGAGED)
            .where(Lead.booked_at.is_(None))
            .where(Lead.first_engaged_at.is_not(None))
            .where(Lead.human_owned.is_(False))
            .limit(s.scheduler_batch_size)
        )
    ).all()

    for lead in rows:
        result.scanned += 1
        phase = lead.engagement_phase(now=now)
        try:
            if phase == "handoff" and not lead.phase_handoff_notified:
                await notify_counselor(
                    deps.session,
                    s,
                    lead,
                    trigger=HandoffTrigger.PHASE_HANDOFF,
                    summary=(
                        "Lead has been engaged ~24-48h without booking and has gone "
                        "quiet. Time for a human nudge."
                    ),
                    context={"source": "scheduler"},
                )
                lead.phase_handoff_notified = True
                result.acted += 1
            elif phase == "nurture":
                await state_machine.apply_event(
                    deps.session,
                    lead,
                    LifecycleEvent.NURTURE_TIMEOUT,
                    actor="scheduler",
                    reason="48h+ engaged without booking",
                    now=now,
                )
                lead.next_reengagement_at = now + timedelta(days=s.nurture_gap_days)
                lead.nurture_round = 0
                result.acted += 1
            else:
                result.skipped += 1
        except Exception:  # noqa: BLE001
            logger.exception("advance_engagement failed for lead %s", lead.id)
            result.errors += 1

    await deps.session.commit()
    logger.info("sweep advance_engagement: %s", result.as_dict())
    return result


# ---------------------------------------------------------------------------
async def send_in_window_nudges(deps: SweepDeps) -> SweepResult:
    s = deps.settings
    now = utcnow()
    result = SweepResult("send_in_window_nudges")

    if not s.bot_autoreply_enabled or s.nudge_max_per_window <= 0:
        return result

    rows = (
        await deps.session.scalars(
            select(Lead)
            .where(Lead.lifecycle_state == LifecycleState.ENGAGED)
            .where(Lead.service_window_expires_at.is_not(None))
            .where(Lead.service_window_expires_at > now)
            .where(Lead.booked_at.is_(None))
            .where(Lead.human_owned.is_(False))
            .where(
                Lead.consent_gate.in_([ConsentGate.CLEARED, ConsentGate.NOT_REQUIRED])
            )
            .where(Lead.nudge_count < s.nudge_max_per_window)
            .limit(s.scheduler_batch_size)
        )
    ).all()

    outreach = _outreach(deps)
    quiet_cutoff = timedelta(hours=s.nudge_after_hours)

    for lead in rows:
        result.scanned += 1
        last_activity = max(
            [t for t in (lead.last_inbound_at, lead.last_nudge_at) if t is not None],
            default=None,
        )
        if last_activity is None or (now - last_activity) < quiet_cutoff:
            result.skipped += 1
            continue
        if lead.engagement_phase(now=now) != "push":
            result.skipped += 1
            continue
        try:
            await outreach.send_text(
                lead,
                text=nudge_text(s, lead.nudge_count),
                actor=SentBy.BOT,
                reason="in-window nudge",
                purpose="reply",
                commit=False,
            )
            lead.last_nudge_at = now
            lead.nudge_count += 1
            result.acted += 1
            logger.info("nudge sent", extra=log_extra(lead=mask_phone(lead.phone_e164)))
        except Exception:  # noqa: BLE001
            logger.exception("nudge failed for lead %s", lead.id)
            result.errors += 1

    await deps.session.commit()
    logger.info("sweep send_in_window_nudges: %s", result.as_dict())
    return result


# ---------------------------------------------------------------------------
async def run_reengagement(deps: SweepDeps) -> SweepResult:
    s = deps.settings
    now = utcnow()
    result = SweepResult("run_reengagement")

    rows = (
        await deps.session.scalars(
            select(Lead)
            .where(
                Lead.lifecycle_state.in_([LifecycleState.SILENT, LifecycleState.NURTURE])
            )
            .where(Lead.next_reengagement_at.is_not(None))
            .where(Lead.next_reengagement_at <= now)
            .where(Lead.consent_status != ConsentStatus.OPTED_OUT)
            .where(Lead.human_owned.is_(False))
            # sales re-engagement only for gate-cleared leads; un-gated leads are
            # the send_consent_asks sweep's job
            .where(
                Lead.consent_gate.in_([ConsentGate.CLEARED, ConsentGate.NOT_REQUIRED])
            )
            .limit(s.scheduler_batch_size)
        )
    ).all()

    outreach = _outreach(deps)

    for lead in rows:
        result.scanned += 1
        try:
            if lead.lifecycle_state == LifecycleState.SILENT:
                acted = await _reengage_silent(deps, outreach, lead, now)
            else:
                acted = await _reengage_nurture(deps, outreach, lead, now)
            result.acted += int(acted)
            result.skipped += int(not acted)
        except Exception:  # noqa: BLE001
            logger.exception("run_reengagement failed for lead %s", lead.id)
            result.errors += 1

    await deps.session.commit()
    logger.info("sweep run_reengagement: %s", result.as_dict())
    return result


async def _reengage_silent(
    deps: SweepDeps, outreach: OutreachService, lead: Lead, now
) -> bool:
    s = deps.settings
    if lead.silent_retry_round >= s.silent_retry_max_rounds_effective:
        await state_machine.apply_event(
            deps.session,
            lead,
            LifecycleEvent.RETRY_ROUNDS_EXHAUSTED,
            actor="scheduler",
            reason=f"{lead.silent_retry_round} re-open rounds, no reply",
            now=now,
        )
        lead.next_reengagement_at = None
        return True

    decision = outreach.evaluate(lead, purpose="outreach")
    if not decision.allowed:
        # can't send (e.g. consent unverified — Phase 1 pending); back off, don't loop
        lead.next_reengagement_at = now + timedelta(
            hours=s.reengagement_delay_hours(lead.silent_retry_round)
        )
        logger.info(
            "re-engagement blocked", extra=log_extra(
                lead=mask_phone(lead.phone_e164), reasons=decision.hard_blocks
            )
        )
        return False

    await outreach.send_template(
        lead,
        template_name=s.reengage_template_name,
        language=s.reengage_template_language,
        category=_template_category(s.reengage_template_category),
        actor=SentBy.BOT,
        reason=f"re-open round {lead.silent_retry_round + 1}",
        idempotency_key=f"reengage:{lead.id}:r{lead.silent_retry_round}",
        purpose="outreach",
        commit=False,
    )
    lead.silent_retry_round += 1
    lead.last_reengagement_at = now
    lead.next_reengagement_at = now + timedelta(
        hours=s.reengagement_delay_hours(lead.silent_retry_round)
    )
    return True


async def _reengage_nurture(
    deps: SweepDeps, outreach: OutreachService, lead: Lead, now
) -> bool:
    s = deps.settings
    if lead.nurture_round >= s.nurture_max_rounds:
        await state_machine.apply_event(
            deps.session,
            lead,
            LifecycleEvent.RETRY_ROUNDS_EXHAUSTED,
            actor="scheduler",
            reason=f"{lead.nurture_round} nurture rounds, no reply",
            now=now,
        )
        lead.next_reengagement_at = None
        return True

    decision = outreach.evaluate(lead, purpose="outreach")
    if not decision.allowed:
        lead.next_reengagement_at = now + timedelta(days=s.nurture_gap_days)
        return False

    await outreach.send_template(
        lead,
        template_name=s.nurture_template_name,
        language=s.nurture_template_language,
        category=_template_category(s.nurture_template_category),
        actor=SentBy.BOT,
        reason=f"nurture round {lead.nurture_round + 1}",
        idempotency_key=f"nurture:{lead.id}:r{lead.nurture_round}",
        purpose="outreach",
        commit=False,
    )
    lead.nurture_round += 1
    lead.last_reengagement_at = now
    lead.next_reengagement_at = now + timedelta(days=s.nurture_gap_days)
    return True


def _template_category(value: str) -> TemplateCategory:
    try:
        return TemplateCategory(value.lower())
    except ValueError:
        return TemplateCategory.UNKNOWN


ALL_SWEEPS = {
    "send_consent_asks": send_consent_asks,
    "expire_windows": expire_windows,
    "advance_engagement": advance_engagement,
    "send_in_window_nudges": send_in_window_nudges,
    "run_reengagement": run_reengagement,
}
