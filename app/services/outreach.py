"""Outreach guard + send orchestration.

`evaluate()` is the single gate every bot-initiated message must pass. Hard
blocks depend on `purpose`:

  purpose="consent_ask" (the in-chat opt-in ask itself — build-plan §2 gate):
  * only blocked by opt-out, a parked gate (minor/needs-human/refused),
    human-owned/handoff, or the lead already being past the opt-in step.
    NOT blocked by unverified consent — this ask IS the consent step.

  purpose="outreach" (business-initiated template / cold message):
  * opted-out lead                -> never contact (critique A1/A3)
  * consent gate not CLEARED      -> blocked (must pass the opt-in + age gate first)
  * consent not verified          -> blocked while OUTREACH_REQUIRE_VERIFIED_CONSENT
                                     (legacy consent audit pending — critique A1)
  * consent unknown / withdrawn   -> blocked
  * detected minor + policy not cleared -> blocked (DPDP undecided — critique A2)
  * human owns the thread / handoff / gate-hold -> bot stays out (critique B6)

  purpose="reply" (bot answering a lead-initiated message inside the 24h window):
  * marketing-consent checks are dropped — the lead messaged us first
  * opt-out, parked-gate, human-owned, handoff, and minor-policy blocks still apply
  * a gate still in progress (pending_opt_in / pending_age) is allowed — the
    conversation engine runs the gate handler, not the sales flow
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import OutreachBlocked, ServiceWindowClosed
from app.logging_config import get_logger
from app.models.enums import (
    ConsentGate,
    ConsentStatus,
    LifecycleEvent,
    LifecycleState,
    MessageStatus,
    MessageType,
    MinorPolicyStatus,
    MinorStatus,
    SentBy,
    TemplateCategory,
)
from app.models.lead import Lead
from app.models.message import Message
from app.services import messages as messages_service
from app.services import state_machine
from app.services.whatsapp.base import WhatsAppClient
from app.services.windows import WindowService

logger = get_logger(__name__)


@dataclass
class ContactDecision:
    lead_id: str
    allowed: bool
    hard_blocks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def raise_if_blocked(self) -> None:
        if not self.allowed:
            raise OutreachBlocked(self)


class OutreachService:
    def __init__(
        self,
        session: AsyncSession,
        redis: Redis,
        settings: Settings,
        wa_client: WhatsAppClient,
    ) -> None:
        self._session = session
        self._settings = settings
        self._wa = wa_client
        self._windows = WindowService(redis, settings)

    # -- the guard -----------------------------------------------------
    def evaluate(
        self,
        lead: Lead,
        *,
        purpose: Literal["outreach", "reply", "consent_ask"] = "outreach",
    ) -> ContactDecision:
        hard: list[str] = []
        warn: list[str] = []

        if (
            lead.consent_status == ConsentStatus.OPTED_OUT
            or lead.lifecycle_state == LifecycleState.OPTED_OUT
        ):
            hard.append("lead_opted_out")

        # A gate that parked the lead for a human blocks EVERYTHING.
        if lead.consent_gate in (ConsentGate.MINOR_HOLD, ConsentGate.NEEDS_HUMAN):
            hard.append(f"consent_gate_{lead.consent_gate.value}")
        if lead.consent_gate == ConsentGate.REFUSED:
            hard.append("consent_gate_refused")

        if purpose == "consent_ask":
            # the opt-in ask itself — only the blocks above apply, plus a
            # already-answered / already-cleared guard
            if lead.consent_gate in (
                ConsentGate.PENDING_AGE,
                ConsentGate.CLEARED,
                ConsentGate.NOT_REQUIRED,
            ):
                hard.append("consent_gate_not_pending_opt_in")
        elif purpose == "outreach":
            # sales / re-engagement outreach needs the gate cleared first
            if lead.consent_gate not in (ConsentGate.CLEARED, ConsentGate.NOT_REQUIRED):
                hard.append("consent_gate_not_cleared")
            if lead.consent_status == ConsentStatus.WITHDRAWN:
                hard.append("consent_withdrawn")
            if lead.consent_status == ConsentStatus.UNKNOWN:
                hard.append("consent_unknown")
            if self._settings.outreach_require_verified_consent and not lead.consent_verified:
                hard.append("consent_not_verified")

        if lead.is_minor == MinorStatus.YES and lead.minor_policy_status in (
            MinorPolicyStatus.PENDING_REVIEW,
            MinorPolicyStatus.BLOCKED,
        ):
            hard.append(f"minor_policy_{lead.minor_policy_status.value}")

        if lead.human_owned:
            hard.append("human_owned")
        if lead.lifecycle_state in (LifecycleState.HANDOFF, LifecycleState.GATE_HOLD):
            hard.append("human_handoff_in_progress")

        if lead.lifecycle_state == LifecycleState.DORMANT:
            warn.append("lead_dormant")

        return ContactDecision(
            lead_id=str(lead.id),
            allowed=not hard,
            hard_blocks=hard,
            warnings=warn,
        )

    def can_contact(
        self, lead: Lead, *, purpose: Literal["outreach", "reply", "consent_ask"] = "outreach"
    ) -> bool:
        return self.evaluate(lead, purpose=purpose).allowed

    # -- sends -------------------------------------------------------
    async def send_template(
        self,
        lead: Lead,
        *,
        template_name: str,
        language: str,
        variables: dict[str, list[str]] | None = None,
        category: TemplateCategory = TemplateCategory.UNKNOWN,
        actor: SentBy = SentBy.BOT,
        reason: str | None = None,
        idempotency_key: str | None = None,
        purpose: Literal["outreach", "reply", "consent_ask"] = "outreach",
        commit: bool = True,
    ) -> Message:
        self.evaluate(lead, purpose=purpose).raise_if_blocked()

        # `persist_outbound` returns the existing row if this key was already
        # sent, so a retried send does not double-message.
        key = idempotency_key or f"tpl:{lead.id}:{template_name}:{language}"
        already_sent = await messages_service.get_outbound_by_idempotency_key(
            self._session, key
        )
        if already_sent is not None:
            return already_sent

        result = await self._wa.send_template(
            to=lead.phone_e164,
            template_name=template_name,
            language=language,
            variables=variables,
        )
        message = await messages_service.persist_outbound(
            self._session,
            lead,
            message_type=MessageType.TEMPLATE,
            template_name=template_name,
            template_language=language,
            template_category=category,
            template_variables=variables,
            to_phone=lead.phone_e164,
            wa_message_id=result.message_id,
            status=MessageStatus.SENT,
            sent_by=actor,
            raw_payload=result.raw,
            idempotency_key=key,
        )
        # A business-initiated template does NOT open the 24h window.
        await state_machine.apply_event(
            self._session,
            lead,
            LifecycleEvent.OUTBOUND_TEMPLATE_SENT,
            actor=actor.value,
            reason=reason or f"template:{template_name}",
        )
        if commit:
            await self._session.commit()
        logger.info("template sent lead=%s template=%s", lead.id, template_name)
        return message

    async def send_text(
        self,
        lead: Lead,
        *,
        text: str,
        actor: SentBy = SentBy.BOT,
        reason: str | None = None,
        require_open_window: bool | None = None,
        purpose: Literal["outreach", "reply", "consent_ask"] = "outreach",
        commit: bool = True,
        now=None,
    ) -> Message:
        self.evaluate(lead, purpose=purpose).raise_if_blocked()

        if require_open_window is None:
            require_open_window = actor == SentBy.BOT
        if require_open_window and not await self._windows.is_open(lead, now=now):
            raise ServiceWindowClosed(lead.id)

        result = await self._wa.send_text(to=lead.phone_e164, text=text)
        message = await messages_service.persist_outbound(
            self._session,
            lead,
            message_type=MessageType.TEXT,
            body=text,
            to_phone=lead.phone_e164,
            wa_message_id=result.message_id,
            status=MessageStatus.SENT,
            sent_by=actor,
            raw_payload=result.raw,
        )
        await state_machine.apply_event(
            self._session,
            lead,
            LifecycleEvent.OUTBOUND_MESSAGE_SENT,
            actor=actor.value,
            reason=reason or "free-form reply",
            now=now,
        )
        if commit:
            await self._session.commit()
        return message
