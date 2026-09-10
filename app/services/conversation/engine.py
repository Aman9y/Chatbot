"""The conversation engine: inbound message -> guarded, gated auto-reply.

Flow (plan Phase 3 + 4):
  gates -> speaker detection -> context build -> LLM draft
        -> Response Guard  (block -> regenerate -> safe fallback)
        -> send via OutreachService (auto-send, fully gated)
        -> booking detection -> HANDOFF + counsellor notification
        -> persist a full ConversationTrace

Engine failure never fails webhook ingestion — the inbound message is already
persisted; the trace records the error and a human can follow up.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.logging_config import get_logger, log_extra, mask_phone
from app.models.conversation_trace import ConversationTrace
from app.models.enums import (
    ConsentGate,
    ConsentMethod,
    ConsentStatus,
    HandoffTrigger,
    LeadScore,
    LifecycleEvent,
    LifecycleState,
    MessageDirection,
    MessageType,
    MinorPolicyStatus,
    MinorStatus,
    RoleHint,
    SentBy,
)
from app.models.lead import Lead
from app.models.message import Message
from app.services import consent as consent_service
from app.services import state_machine
from app.services.conversation import consent_gate as gate_copy
from app.services.conversation.booking import BookingSignal, detect_booking
from app.services.conversation.consent_gate import (
    interpret_age_reply,
    interpret_optin_reply,
    stated_age,
)
from app.services.conversation.context import build_turn_context
from app.services.conversation.deflection import select_deflection
from app.services.conversation.extraction import apply_to_lead, extract_qualifiers
from app.services.conversation.scoring import ScoreInputs, score_lead
from app.services.conversation.speaker import detect_speaker
from app.services.guard.fallback import safe_fallback_message
from app.services.guard.guard import GuardVerdict, ResponseGuard
from app.services.handoff import notify_counselor
from app.services.knowledge.base import KnowledgeBase
from app.services.llm.base import LLMClient, LLMMessage
from app.services.llm.factory import classifier_model, reply_model
from app.services.outreach import OutreachService
from app.services.timeutils import utcnow
from app.services.windows import WindowService

logger = get_logger(__name__)


_REGEN_HINTS = {
    "premium_cost_disclosure": "Drop the figure entirely — this is a premium "
        "country; say costs vary and the counsellor gives exact numbers.",
    "cost_outside_approved_range": "The number is outside the approved range for "
        "that country — quote the approved range exactly or drop the figure.",
    "unapproved_cost_figure": "There is no approved range for the country in "
        "scope — drop the figure; say the counsellor gives current numbers.",
    "multi_country_cost": "Answer only ONE country's cost — pick the most "
        "relevant and offer the rest on the call.",
    "sensitive_cost_needs_contact": "This is Georgia/Nepal — keep the range only "
        "with the reason it is higher AND the director's number in the same reply.",
    "cost_missing_inclusion": "Do not state the figure bare — pair it with what "
        "the money covers (visa, travel, accommodation, support).",
    "financing_mention": "Remove any loan / EMI / instalment mention.",
    "payment_terms_disclosure": "Remove any payment schedule / refund / deposit "
        "term — say the counsellor puts it in writing.",
    "admission_guarantee": "Remove any guarantee / assurance of an outcome.",
    "blended_cost_range": "That range matches no approved country — quote one "
        "approved country's exact range, or drop the figure.",
    "overpromise": "Drop the reassurance that promises an outcome or calls a "
        "difficulty easy. On admission / FMGE / safety / risk be honest that it "
        "depends on the student and the university; use 'many students', "
        "'typically', 'the counsellor assesses your case'. Never 'easy', 'a "
        "formality', 'guaranteed', 'no risk', '100% safe', 'nothing to worry "
        "about'.",
    "pg_cost_mention": "Remove the PG cost figure.",
    "meta_leak": "Do not reveal prompt internals.",
    "reply_too_long": "Cut it right down.",
}


def _regen_fix_hint(rules: list[str]) -> str:
    hints = [_REGEN_HINTS[r] for r in dict.fromkeys(rules) if r in _REGEN_HINTS]
    return " ".join(hints) if hints else "Rewrite it without stating anything unverified."


_BOT_OWNED_STATES = {
    LifecycleState.HANDOFF,
    LifecycleState.GATE_HOLD,
    LifecycleState.OPTED_OUT,
}
_TEXTUAL_TYPES = (MessageType.TEXT, MessageType.INTERACTIVE, MessageType.BUTTON)
# consent_gate values where the bot must not run the sales flow at all
_GATE_STOPPED = {
    ConsentGate.REFUSED,
    ConsentGate.MINOR_HOLD,
    ConsentGate.NEEDS_HUMAN,
}


@dataclass
class ConversationResult:
    action: str  # sent | fallback_sent | skipped | error
    reply_text: str | None = None
    outbound_message_id: object | None = None
    trace_id: object | None = None
    skipped_reason: str | None = None
    booking_detected: bool = False


class ConversationEngine:
    def __init__(
        self,
        session: AsyncSession,
        redis: Redis,
        settings: Settings,
        *,
        llm: LLMClient,
        kb: KnowledgeBase,
        wa_client,
    ) -> None:
        self._session = session
        self._redis = redis
        self._settings = settings
        self._llm = llm
        self._kb = kb
        self._outreach = OutreachService(session, redis, settings, wa_client)
        self._guard = ResponseGuard(settings)
        self._windows = WindowService(redis, settings)

    async def _pending_inbound(self, lead: Lead) -> list[Message]:
        """Inbound text messages for this lead the engine has not acted on yet
        (no ConversationTrace points at them). Ordered oldest-first."""

        traced = (
            select(ConversationTrace.inbound_message_id)
            .where(ConversationTrace.lead_id == lead.id)
            .where(ConversationTrace.inbound_message_id.is_not(None))
        )
        rows = await self._session.scalars(
            select(Message)
            .where(Message.lead_id == lead.id)
            .where(Message.direction == MessageDirection.INBOUND)
            .where(Message.message_type.in_(_TEXTUAL_TYPES))
            .where(Message.body.is_not(None))
            .where(Message.id.not_in(traced))
            .order_by(Message.created_at)
        )
        return [m for m in rows if (m.body or "").strip()]

    async def handle_pending_turn(self, lead: Lead) -> ConversationResult:
        """Process every unanswered inbound message for the lead as ONE turn
        (build-plan §3.2 debounce/merge). Called by the turn dispatcher after the
        debounce window; the per-lead lock is already held by the caller."""

        pending = await self._pending_inbound(lead)
        if not pending:
            return ConversationResult("skipped", skipped_reason="no_pending_inbound")

        anchor = pending[-1]
        merged_text = "\n".join(
            (m.body or "").strip() for m in pending if (m.body or "").strip()
        )
        override = merged_text if len(pending) > 1 else None
        result = await self.handle_inbound(lead, anchor, override_text=override)

        if len(pending) > 1:
            now = utcnow()
            for m in pending[:-1]:
                self._session.add(
                    ConversationTrace(
                        lead_id=lead.id,
                        inbound_message_id=m.id,
                        final_action="merged",
                        skipped_reason=f"merged_into:{result.trace_id}",
                        started_at=now,
                        finished_at=now,
                    )
                )
            await self._session.commit()
            logger.info(
                "merged %d rapid-fire messages into one turn", len(pending)
            )
        return result

    async def handle_inbound(
        self, lead: Lead, inbound_message: Message, *, override_text: str | None = None
    ) -> ConversationResult:
        """Entry point. Assumes the inbound message is already committed by the
        webhook processor; runs in its own transaction and never raises."""

        # Capture ids up front — session.rollback() expires ORM instances, and
        # re-loading them would need IO outside the async context.
        lead_id = lead.id
        message_id = inbound_message.id

        try:
            return await self._run_with_trace(lead, inbound_message, override_text)
        except Exception as exc:  # noqa: BLE001 - never propagate into webhook ingestion
            logger.exception("conversation engine failed for lead %s", lead_id)
            await self._session.rollback()
            failed = ConversationTrace(
                lead_id=lead_id,
                inbound_message_id=message_id,
                final_action="error",
                error=str(exc)[:4000],
                started_at=utcnow(),
                finished_at=utcnow(),
            )
            self._session.add(failed)
            await self._session.commit()
            failed_id = failed.id
            return ConversationResult(
                "error", trace_id=failed_id, skipped_reason="engine_error"
            )

    async def _run_with_trace(
        self, lead: Lead, inbound_message: Message, override_text: str | None = None
    ) -> ConversationResult:
        trace = ConversationTrace(
            lead_id=lead.id,
            inbound_message_id=inbound_message.id,
            lifecycle_state=lead.lifecycle_state.value,
            started_at=utcnow(),
        )
        self._session.add(trace)
        await self._session.flush()

        result = await self._run(lead, inbound_message, trace, override_text)

        trace.finished_at = utcnow()
        await self._session.commit()
        return result

    # -- internals -----------------------------------------------------
    def _skip(self, trace: ConversationTrace, reason: str) -> ConversationResult:
        trace.final_action = "skipped"
        trace.skipped_reason = reason
        logger.info("conversation skipped: %s", reason)
        return ConversationResult("skipped", trace_id=trace.id, skipped_reason=reason)

    async def _run(
        self,
        lead: Lead,
        inbound_message: Message,
        trace: ConversationTrace,
        override_text: str | None = None,
    ) -> ConversationResult:
        s = self._settings

        if not s.bot_autoreply_enabled:
            return self._skip(trace, "autoreply_disabled")
        if lead.lifecycle_state in _BOT_OWNED_STATES or lead.human_owned:
            return self._skip(trace, f"not_bot_owned:{lead.lifecycle_state.value}")

        text = (
            override_text if override_text is not None else (inbound_message.body or "")
        ).strip()
        if not text:
            return self._skip(trace, "no_text")

        if not await self._windows.is_open(lead):
            return self._skip(trace, "window_closed")

        reply_decision = self._outreach.evaluate(lead, purpose="reply")
        if not reply_decision.allowed:
            reason = ",".join(reply_decision.hard_blocks)
            if any(b.startswith("minor_policy") for b in reply_decision.hard_blocks):
                await notify_counselor(
                    self._session,
                    s,
                    lead,
                    trigger=HandoffTrigger.MANUAL,
                    summary=(
                        "Minor lead messaged in; bot auto-reply is blocked by minor "
                        f"policy. Lead said: {text[:200]}"
                    ),
                    context={"trace_id": str(trace.id)},
                )
            return self._skip(trace, f"reply_blocked:{reason}")

        # --- consent + age gate (build-plan §2 / DPDP) ------------------
        # Nothing sells to a lead until they opt in AND confirm 18+.
        if s.consent_gate_enabled:
            gate = lead.consent_gate
            if gate in _GATE_STOPPED:
                return self._skip(trace, f"consent_gate:{gate.value}")
            if gate not in (ConsentGate.CLEARED, ConsentGate.NOT_REQUIRED):
                gate_result = await self._run_consent_gate(lead, inbound_message, trace, text)
                if gate_result is not None:
                    return gate_result
                # gate just cleared on an 18+ answer — fall through to the sales flow

        return await self._run_sales(lead, inbound_message, trace, text)

    async def _run_sales(
        self, lead: Lead, inbound_message: Message, trace: ConversationTrace, text: str
    ) -> ConversationResult:
        s = self._settings

        # Lazy NURTURE transition at 48h+ (plan §5; timed firing is Phase 5).
        if (
            lead.engagement_phase() == "nurture"
            and lead.lifecycle_state == LifecycleState.ENGAGED
        ):
            await state_machine.apply_event(
                self._session,
                lead,
                LifecycleEvent.NURTURE_TIMEOUT,
                actor="system",
                reason="48h+ engaged without booking",
            )

        speaker, method = await detect_speaker(
            text,
            prior_role=lead.role_hint,
            llm=self._llm,
            model=classifier_model(s),
            use_llm=(s.llm_provider != "fake"),
        )
        if speaker != RoleHint.UNKNOWN and lead.role_hint == RoleHint.UNKNOWN:
            lead.role_hint = speaker
        trace.speaker_detected = speaker
        trace.speaker_method = method

        # Micro-qualification (sales-playbook Part 6) — pull the counsellor-useful
        # facts out of the lead's own message, before we build the prompt so the
        # fresh profile is in context. Never fails the turn.
        try:
            extraction = await extract_qualifiers(
                text,
                speaker=speaker,
                llm=self._llm,
                model=classifier_model(s),
                use_llm=(s.llm_provider != "fake"),
            )
            qualifier_changes = apply_to_lead(lead, extraction, s)
        except Exception:  # noqa: BLE001 - extraction is best-effort
            logger.exception("qualifier extraction failed for lead %s", lead.id)
            qualifier_changes = {}
        trace.extracted_qualifiers = qualifier_changes or None

        turn = await build_turn_context(
            self._session,
            lead,
            settings=s,
            kb=self._kb,
            speaker=speaker,
            speaker_method=method,
            latest_text=text,
            minutes_since_last_bot=self._minutes_since_last_bot(lead),
        )
        trace.engagement_phase = turn.engagement_phase
        trace.kb_chunk_ids = [c.id for c in turn.kb_chunks]
        trace.llm_provider = self._llm.provider
        trace.llm_model = reply_model(s)
        trace.turn_signals = {
            **(trace.turn_signals or {}),
            "pace": turn.pace_plan.pace if turn.pace_plan else None,
            "message_depth": turn.pace_plan.message_depth if turn.pace_plan else None,
            "tone_stage": turn.pace_plan.tone_stage if turn.pace_plan else None,
            "cta_mode": turn.pace_plan.cta_mode if turn.pace_plan else None,
            "topic": turn.topic_match.rule.id if turn.topic_match else None,
            "handling": turn.topic_match.handling if turn.topic_match else None,
            "hard_deflect_also": [
                r.id for r in (turn.topic_match.hard_deflect_topics if turn.topic_match else [])
            ],
            "high_intent": bool(turn.topic_match and turn.topic_match.high_intent),
            "objection": turn.objection.id if turn.objection else None,
            "deflection": (
                {
                    "mode": turn.deflection.mode.num,
                    "reason": turn.deflection.reason,
                    "contact": turn.deflection.contact,
                    "deflect_index": turn.deflection.deflect_index,
                }
                if turn.deflection
                else None
            ),
        }

        final_text, verdict, used_fallback = await self._generate_guarded(turn, trace)

        if used_fallback:
            await notify_counselor(
                self._session,
                s,
                lead,
                trigger=HandoffTrigger.GUARD_FALLBACK,
                summary=(
                    "Bot could not produce a compliant reply "
                    f"(blocked: {', '.join(verdict.rules) if verdict else 'unknown'}). "
                    f"Lead said: {text[:200]}"
                ),
                context={"trace_id": str(trace.id)},
            )

        outbound = await self._outreach.send_text(
            lead,
            text=final_text,
            actor=SentBy.BOT,
            reason="conversation engine reply",
            purpose="reply",
            commit=False,
        )
        trace.outbound_message_id = outbound.id
        trace.final_text = final_text
        trace.final_action = "fallback_sent" if used_fallback else "sent"

        booking = BookingSignal(detected=False)
        if s.booking_detection_enabled and not used_fallback:
            booking = await detect_booking(
                turn.messages,
                llm=self._llm,
                model=classifier_model(s),
                use_llm=(s.llm_provider != "fake"),
            )
        trace.booking_detected = booking.detected
        trace.booking_details = {
            "method": booking.method,
            "time": booking.proposed_time,
            "format": booking.format,
        }

        # Lead scoring (plan §3 pipeline) — derived signals for the counsellor
        # queue. Best-effort; a scoring failure must not lose the reply.
        try:
            score_result = self._score_lead(lead, turn, text, booking.detected)
            lead.interest_temperature = score_result.temperature
            if lead.lead_score != score_result.score:
                lead.lead_score = score_result.score
            lead.lead_score_reason = score_result.reason[:255]
            lead.lead_score_updated_at = utcnow()
            trace.lead_score = score_result.score.value
            trace.lead_score_reason = score_result.reason[:255]
            trace.interest_temperature = score_result.temperature.value
        except Exception:  # noqa: BLE001
            logger.exception("lead scoring failed for lead %s", lead.id)
            score_result = None

        if booking.detected and lead.lifecycle_state in (
            LifecycleState.ENGAGED,
            LifecycleState.NURTURE,
        ):
            await state_machine.apply_event(
                self._session,
                lead,
                LifecycleEvent.BOOKING_CONFIRMED,
                actor="bot",
                reason=f"lead agreed to {booking.format or 'call'}",
            )
            await notify_counselor(
                self._session,
                s,
                lead,
                trigger=HandoffTrigger.BOOKING,
                summary=(
                    f"Lead agreed to a {booking.format or 'call'}"
                    + (f" ({booking.proposed_time})" if booking.proposed_time else "")
                    + f". Last message: {text[:200]}"
                ),
                context={
                    "trace_id": str(trace.id),
                    "format": booking.format,
                    "time": booking.proposed_time,
                },
            )
        elif (
            score_result is not None
            and score_result.score == LeadScore.HIGH
            and not lead.counsellor_cta_sent
            and lead.lifecycle_state == LifecycleState.ENGAGED
        ):
            await notify_counselor(
                self._session,
                s,
                lead,
                trigger=HandoffTrigger.HIGH_INTENT,
                summary=(
                    "Hot qualified lead — worth an early counsellor reach-out. "
                    f"{score_result.reason}. "
                    f"Profile: {turn.profile_summary}. Last message: {text[:160]}"
                ),
                context={
                    "trace_id": str(trace.id),
                    "lead_score": score_result.score.value,
                    "temperature": score_result.temperature.value,
                },
            )
            lead.counsellor_cta_sent = True
        elif turn.engagement_phase == "handoff" and not lead.phase_handoff_notified:
            await notify_counselor(
                self._session,
                s,
                lead,
                trigger=HandoffTrigger.PHASE_HANDOFF,
                summary=(
                    "Lead engaged ~24-48h without booking — needs a human nudge. "
                    f"Last message: {text[:200]}"
                ),
                context={"trace_id": str(trace.id)},
            )
            lead.phase_handoff_notified = True

        logger.info(
            "conversation reply sent",
            extra=log_extra(
                lead=mask_phone(lead.phone_e164),
                action=trace.final_action,
                attempts=trace.draft_attempts,
                booking=booking.detected,
            ),
        )
        return ConversationResult(
            trace.final_action,
            reply_text=final_text,
            outbound_message_id=outbound.id,
            trace_id=trace.id,
            booking_detected=booking.detected,
        )

    # -- consent + age gate ------------------------------------------
    async def _run_consent_gate(
        self, lead: Lead, inbound_message: Message, trace: ConversationTrace, text: str
    ) -> ConversationResult | None:
        """Handle one gate turn. Returns a terminal ConversationResult, or None
        when the age gate just cleared (caller falls through to the sales flow)."""

        s = self._settings
        use_llm = s.llm_provider != "fake"
        model = classifier_model(s)
        stage = lead.consent_gate

        if stage == ConsentGate.PENDING_OPT_IN:
            # Lead messaged us first (no opt-in ask was sent) -> opt-in is implicit;
            # go straight to the age question.
            if lead.consent_ask_sent_at is None:
                await self._gate_record_opt_in(lead, text)
                lead.consent_gate = ConsentGate.PENDING_AGE
                lead.gate_reask_count = 0
                self._gate_signal(trace, "opt_in", "implicit", 0)
                return await self._gate_reply(
                    lead, trace, gate_copy.age_ask(s), "gate_age_ask"
                )
            verdict = await interpret_optin_reply(
                text, llm=self._llm, model=model, use_llm=use_llm
            )
            self._gate_signal(trace, "opt_in", verdict, lead.gate_reask_count)
            if verdict == "yes":
                await self._gate_record_opt_in(lead, text)
                lead.consent_gate = ConsentGate.PENDING_AGE
                lead.gate_reask_count = 0
                return await self._gate_reply(
                    lead, trace, gate_copy.age_ask(s), "gate_age_ask"
                )
            if verdict == "no":
                return await self._gate_decline(lead, inbound_message, trace, text)
            return await self._gate_unclear(lead, trace, "opt_in")

        # PENDING_AGE — a clear "no / not interested" here still opts the lead out
        if await interpret_optin_reply(
            text, llm=self._llm, model=model, use_llm=use_llm
        ) == "no":
            self._gate_signal(trace, "age", "declined", lead.gate_reask_count)
            return await self._gate_decline(lead, inbound_message, trace, text)

        verdict = await interpret_age_reply(
            text, llm=self._llm, model=model, use_llm=use_llm
        )
        self._gate_signal(trace, "age", verdict, lead.gate_reask_count)
        if verdict == "adult":
            self._gate_clear_adult(lead, text)
            logger.info("consent gate cleared for lead %s", lead.id)
            return None  # -> sales flow
        if verdict == "minor":
            return await self._gate_minor_hold(lead, trace, text)
        return await self._gate_unclear(lead, trace, "age")

    @staticmethod
    def _gate_signal(
        trace: ConversationTrace, stage: str, verdict: str, reask: int
    ) -> None:
        trace.turn_signals = {
            **(trace.turn_signals or {}),
            "gate": {"stage": stage, "verdict": verdict, "reask_count": reask},
        }

    async def _gate_reply(
        self, lead: Lead, trace: ConversationTrace, message: str, action: str
    ) -> ConversationResult:
        outbound = await self._outreach.send_text(
            lead,
            text=message,
            actor=SentBy.BOT,
            reason=f"consent gate: {action}",
            purpose="reply",
            commit=False,
        )
        trace.outbound_message_id = outbound.id
        trace.final_text = message
        trace.final_action = action
        return ConversationResult(
            action,
            reply_text=message,
            outbound_message_id=outbound.id,
            trace_id=trace.id,
        )

    async def _gate_record_opt_in(self, lead: Lead, text: str) -> None:
        await consent_service.record_consent(
            self._session,
            lead,
            status=ConsentStatus.OPTED_IN,
            method=ConsentMethod.CONVERSATIONAL_GATE,
            verified=True,
            consent_text=text[:1000],
            source_reference="in_chat_opt_in",
            notes="Replied yes to the in-chat opt-in ask (build-plan §2 gate).",
        )

    def _gate_clear_adult(self, lead: Lead, text: str) -> None:
        lead.consent_gate = ConsentGate.CLEARED
        lead.gate_reask_count = 0
        lead.is_minor = MinorStatus.NO
        lead.minor_policy_status = MinorPolicyStatus.NOT_APPLICABLE
        age = stated_age(text)
        if age is not None and lead.age_years is None:
            lead.age_years = age
        # opting in via the gate is a verified, affirmative opt-in
        lead.consent_status = ConsentStatus.OPTED_IN
        lead.consent_verified = True

    async def _gate_decline(
        self, lead: Lead, inbound_message: Message, trace: ConversationTrace, text: str
    ) -> ConversationResult:
        # send the respectful close BEFORE opting out / flipping the gate — both
        # of those block outbound sends
        result = await self._gate_reply(
            lead, trace, gate_copy.decline_close(self._settings), "gate_declined"
        )
        lead.consent_gate = ConsentGate.REFUSED
        await consent_service.opt_out(
            self._session,
            lead,
            reason_text=text,
            method=ConsentMethod.CONVERSATIONAL_GATE,
            inbound_message=inbound_message,
            actor="lead",
        )
        return result

    async def _gate_minor_hold(
        self, lead: Lead, trace: ConversationTrace, text: str
    ) -> ConversationResult:
        # send the holding message BEFORE flipping to a state the guard blocks
        result = await self._gate_reply(
            lead, trace, gate_copy.minor_hold_message(self._settings), "gate_minor_hold"
        )
        lead.consent_gate = ConsentGate.MINOR_HOLD
        lead.is_minor = MinorStatus.YES
        lead.minor_policy_status = MinorPolicyStatus.PENDING_REVIEW
        age = stated_age(text)
        if age is not None:
            lead.age_years = age
        await self._gate_park(lead, trace, HandoffTrigger.MINOR_HOLD, text, minor=True)
        return result

    async def _gate_unclear(
        self, lead: Lead, trace: ConversationTrace, stage: str
    ) -> ConversationResult:
        s = self._settings
        if lead.gate_reask_count < 1:
            lead.gate_reask_count += 1
            msg = (
                gate_copy.consent_reask(s)
                if stage == "opt_in"
                else gate_copy.age_reask(s)
            )
            return await self._gate_reply(lead, trace, msg, "gate_reask")
        # send the review notice BEFORE flipping to a state the guard blocks
        result = await self._gate_reply(
            lead, trace, gate_copy.review_hold_message(s), "gate_review"
        )
        lead.consent_gate = ConsentGate.NEEDS_HUMAN
        await self._gate_park(
            lead, trace, HandoffTrigger.CONSENT_REVIEW, f"stage={stage}", minor=False
        )
        return result

    async def _gate_park(
        self,
        lead: Lead,
        trace: ConversationTrace,
        trigger: HandoffTrigger,
        detail: str,
        *,
        minor: bool,
    ) -> None:
        if state_machine.is_allowed(lead.lifecycle_state, LifecycleEvent.GATE_HELD):
            await state_machine.apply_event(
                self._session,
                lead,
                LifecycleEvent.GATE_HELD,
                actor="system",
                reason=trigger.value,
            )
        summary = (
            "Confirmed under-18 in the age gate — held pending guidance. Bot has "
            "stopped; needs the parent/guardian process. "
            if minor
            else "Opt-in/age answer was unreadable twice — bot parked the lead for "
            "review rather than assuming consent. "
        )
        await notify_counselor(
            self._session,
            self._settings,
            lead,
            trigger=trigger,
            summary=summary + f"({detail[:160]})",
            context={"trace_id": str(trace.id), "consent_gate": lead.consent_gate.value},
        )

    @staticmethod
    def _minutes_since_last_bot(lead: Lead) -> float | None:
        """How long after our last message did the lead reply — the pace signal."""

        if lead.last_inbound_at and lead.last_outbound_at:
            delta = (lead.last_inbound_at - lead.last_outbound_at).total_seconds() / 60
            if delta >= 0:
                return delta
        return None

    def _score_lead(self, lead: Lead, turn, text: str, booking_detected: bool):
        inbound_count = sum(1 for m in turn.messages if m.role == "user")
        return score_lead(
            lead,
            ScoreInputs(
                latest_text=text,
                inbound_count=inbound_count,
                minutes_since_last_bot=self._minutes_since_last_bot(lead),
                booking_detected=booking_detected,
                engagement_phase=turn.engagement_phase,
            ),
        )

    async def _generate_guarded(
        self, turn, trace: ConversationTrace
    ) -> tuple[str, GuardVerdict | None, bool]:
        s = self._settings
        drafts: list[dict] = []
        messages = list(turn.messages)
        verdict: GuardVerdict | None = None
        in_tok = out_tok = calls = 0
        latency = 0.0

        for attempt in range(s.guard_regenerate_attempts + 1):
            resp = await self._llm.complete(
                system=turn.system_prompt,
                messages=messages,
                model=reply_model(s),
                max_output_tokens=s.llm_max_output_tokens,
                temperature=s.llm_temperature,
                purpose="reply",
            )
            calls += 1
            in_tok += resp.input_tokens
            out_tok += resp.output_tokens
            latency += resp.latency_ms
            draft = (resp.text or "").strip()

            if s.guard_enabled:
                verdict = self._guard.check(draft, context=turn.guard_context)
            else:
                verdict = GuardVerdict(allowed=True, violations=[], checked_text=draft)
            drafts.append(
                {"attempt": attempt + 1, "text": draft, "verdict": verdict.as_dict()}
            )
            if verdict.allowed:
                trace.draft_attempts = len(drafts)
                trace.drafts = drafts
                trace.guard_violations = []
                trace.guard_verdict = "allowed" if attempt == 0 else "allowed_after_regen"
                self._record_llm(trace, calls, in_tok, out_tok, latency)
                return draft, verdict, False

            messages = list(turn.messages) + [
                LLMMessage(
                    role="user",
                    content=(
                        "(Your previous reply was blocked by the safety check for: "
                        f"{', '.join(verdict.rules)}. "
                        + _regen_fix_hint(verdict.rules)
                        + " Keep it short, acknowledge briefly, and move toward "
                        "booking a call.)"
                    ),
                )
            ]

        trace.draft_attempts = len(drafts)
        trace.drafts = drafts
        trace.guard_violations = verdict.as_dict()["violations"] if verdict else []
        trace.guard_verdict = "blocked_fallback"
        self._record_llm(trace, calls, in_tok, out_tok, latency)

        fallback_plan = select_deflection(
            topic_match=turn.topic_match,
            objection=turn.objection,
            speaker=turn.speaker,
            deflect_index=turn.deflection.deflect_index if turn.deflection else 0,
            guard_blocked_rules=verdict.rules if verdict else None,
        )
        if fallback_plan is not None:
            trace.turn_signals = {
                **(trace.turn_signals or {}),
                "deflection": {
                    "mode": fallback_plan.mode.num,
                    "reason": fallback_plan.reason,
                    "contact": fallback_plan.contact,
                    "deflect_index": fallback_plan.deflect_index,
                },
            }
        return (
            safe_fallback_message(
                s,
                engagement_phase=turn.engagement_phase,
                plan=fallback_plan,
                prior_bot_text=turn.guard_context.prior_bot_text,
            ),
            verdict,
            True,
        )

    @staticmethod
    def _record_llm(
        trace: ConversationTrace, calls: int, in_tok: int, out_tok: int, latency: float
    ) -> None:
        trace.llm_calls = calls
        trace.llm_input_tokens = in_tok
        trace.llm_output_tokens = out_tok
        trace.llm_latency_ms = Decimal(str(round(latency, 2)))
