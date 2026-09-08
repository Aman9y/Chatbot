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
    HandoffTrigger,
    LifecycleEvent,
    LifecycleState,
    MessageDirection,
    MessageType,
    RoleHint,
    SentBy,
)
from app.models.lead import Lead
from app.models.message import Message
from app.services import state_machine
from app.services.conversation.booking import BookingSignal, detect_booking
from app.services.conversation.context import build_turn_context
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

_BOT_OWNED_STATES = {LifecycleState.HANDOFF, LifecycleState.OPTED_OUT}
_TEXTUAL_TYPES = (MessageType.TEXT, MessageType.INTERACTIVE, MessageType.BUTTON)


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

        turn = await build_turn_context(
            self._session,
            lead,
            settings=s,
            kb=self._kb,
            speaker=speaker,
            speaker_method=method,
            latest_text=text,
        )
        trace.engagement_phase = turn.engagement_phase
        trace.kb_chunk_ids = [c.id for c in turn.kb_chunks]
        trace.llm_provider = self._llm.provider
        trace.llm_model = reply_model(s)

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
                        f"{', '.join(verdict.rules)}. Rewrite it: shorter, no cost "
                        "figures or ranges, no guarantees, no loan/EMI mention, no PG "
                        "cost. Acknowledge briefly and move toward booking a call.)"
                    ),
                )
            ]

        trace.draft_attempts = len(drafts)
        trace.drafts = drafts
        trace.guard_violations = verdict.as_dict()["violations"] if verdict else []
        trace.guard_verdict = "blocked_fallback"
        self._record_llm(trace, calls, in_tok, out_tok, latency)
        return (
            safe_fallback_message(s, engagement_phase=turn.engagement_phase),
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
