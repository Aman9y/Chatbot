"""Core inbound webhook processing.

Responsibilities:
  * signature verification
  * payload-hash dedup (idempotency keyed on ``processed``, not existence)
  * per-message wamid dedup, per-status advance-only reconciliation
  * STOP / opt-out short-circuit BEFORE any normal processing (critique A3)
  * lead find-or-create, message persistence, 24h window, state transitions
  * AFTER the main commit, hand each new inbound message to the conversation
    engine (Phase 3) — a failure there never fails ingestion.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.logging_config import get_logger, log_extra, mask_phone
from app.models.enums import (
    LifecycleEvent,
    LifecycleState,
    MessageStatus,
    MessageType,
)
from app.models.lead import Lead
from app.models.message import Message
from app.models.webhook_event import WebhookEvent
from app.schemas.webhook import (
    ChangeValue,
    InboundMessage,
    StatusUpdate,
    WebhookEnvelope,
)
from app.security.signature import verify_signature
from app.services import consent as consent_service
from app.services import leads as leads_service
from app.services import messages as messages_service
from app.services import state_machine
from app.services.knowledge.base import KnowledgeBase
from app.services.llm.base import LLMClient
from app.services.phone import PhoneNormalizationError, normalize_wa_id
from app.services.stop_keywords import is_opt_out, load_keywords
from app.services.timeutils import from_unix, utcnow
from app.services.whatsapp.base import WhatsAppClient
from app.services.windows import WindowService

logger = get_logger(__name__)

_MESSAGE_TYPE_MAP = {
    "text": MessageType.TEXT,
    "image": MessageType.IMAGE,
    "video": MessageType.VIDEO,
    "audio": MessageType.AUDIO,
    "voice": MessageType.AUDIO,
    "document": MessageType.DOCUMENT,
    "sticker": MessageType.STICKER,
    "location": MessageType.LOCATION,
    "contacts": MessageType.CONTACTS,
    "interactive": MessageType.INTERACTIVE,
    "button": MessageType.BUTTON,
    "reaction": MessageType.REACTION,
    "order": MessageType.ORDER,
    "system": MessageType.SYSTEM,
    "unsupported": MessageType.UNSUPPORTED,
}

_STATUS_MAP = {
    "sent": MessageStatus.SENT,
    "delivered": MessageStatus.DELIVERED,
    "read": MessageStatus.READ,
    "failed": MessageStatus.FAILED,
    "deleted": MessageStatus.DELETED,
    "warning": MessageStatus.SENT,
}

_REDACT_HEADERS = {"x-hub-signature-256", "authorization", "cookie"}


@dataclass
class IngestResult:
    status: str
    http_status: int
    event_id: str | None = None
    detail: str | None = None


def _extract_text(message: InboundMessage) -> str | None:
    if message.text and message.text.body:
        return message.text.body
    if message.button and message.button.text:
        return message.button.text
    if message.interactive:
        for reply in (message.interactive.button_reply, message.interactive.list_reply):
            if reply and (reply.title or reply.id):
                return reply.title or reply.id
    return None


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        k: ("<redacted>" if k.lower() in _REDACT_HEADERS else v) for k, v in headers.items()
    }


class WebhookProcessor:
    def __init__(
        self,
        session: AsyncSession,
        redis: Redis,
        settings: Settings,
        *,
        llm: LLMClient | None = None,
        kb: KnowledgeBase | None = None,
        wa_client: WhatsAppClient | None = None,
    ) -> None:
        self._session = session
        self._redis = redis
        self._settings = settings
        self._windows = WindowService(redis, settings)
        self._stop_keywords = load_keywords(settings.stop_keywords)
        self._llm = llm
        self._kb = kb
        self._wa_client = wa_client
        # (lead_id, inbound_message_id) pairs to hand to the conversation engine
        # AFTER the webhook's own work is committed.
        self._pending_conversations: list[tuple[uuid.UUID, uuid.UUID]] = []

    @property
    def _conversation_enabled(self) -> bool:
        return (
            self._llm is not None
            and self._kb is not None
            and self._wa_client is not None
        )

    # -- entrypoint -------------------------------------------------------
    async def ingest(
        self,
        *,
        raw_body: bytes,
        signature_header: str | None,
        headers: dict[str, str],
        source_ip: str | None,
    ) -> IngestResult:
        signature_valid = verify_signature(
            self._settings.meta_app_secret, raw_body, signature_header
        )
        event_hash = hashlib.sha256(raw_body).hexdigest()

        existing = await self._session.scalar(
            select(WebhookEvent).where(WebhookEvent.event_hash == event_hash)
        )
        if existing is not None and existing.processed:
            return IngestResult("duplicate", 200, str(existing.id))

        if existing is None:
            event = await self._create_event(
                event_hash, signature_valid, raw_body, headers, source_ip
            )
        else:
            event = existing
        event.processing_attempts += 1
        # Persist the event row NOW so it survives a later processing rollback
        # (idempotency is keyed on `processed`, so an unprocessed row is retried).
        await self._session.commit()

        # Meta signs every webhook with X-Hub-Signature-256 (HMAC of
        # meta_app_secret); 360dialog does not (there is no "your Meta app"
        # holding a secret in that setup) — so this rejection is only
        # enforced when settings.webhook_signature_required is true (the
        # default; set false for a 360dialog source). signature_valid is
        # still recorded on the event either way, for the audit trail.
        if not signature_valid and self._settings.webhook_signature_required:
            event.processing_error = "invalid signature"
            await self._session.commit()
            logger.warning("webhook rejected: invalid signature", extra=log_extra(ip=source_ip))
            return IngestResult("invalid_signature", 403, str(event.id))

        try:
            envelope = WebhookEnvelope.model_validate_json(raw_body)
        except ValidationError as exc:
            event.processed = True
            event.processed_at = utcnow()
            event.processing_error = f"payload validation failed: {exc}"
            await self._session.commit()
            logger.warning("webhook payload failed validation")
            # 200: a malformed body will not become valid on retry.
            return IngestResult("invalid_payload", 200, str(event.id))

        event.object_type = envelope.object
        event.raw_body = envelope.model_dump(mode="json")
        event.entry_count = len(envelope.entry)

        try:
            msg_count, status_count = await self._process(envelope, event)
        except Exception as exc:  # noqa: BLE001 - want to record + 500 for retry
            await self._session.rollback()
            await self._record_failure(event_hash, str(exc))
            logger.exception("webhook processing failed")
            return IngestResult("processing_error", 500, None, detail=str(exc))

        event.message_count = msg_count
        event.status_count = status_count
        event.processed = True
        event.processed_at = utcnow()
        event.processing_error = None
        await self._session.commit()
        # Capture the id now: the conversation engine may roll the session back on
        # its own errors, which expires this ORM instance.
        event_id = str(event.id)

        await self._run_pending_conversations()

        return IngestResult("processed", 200, event_id)

    async def _run_pending_conversations(self) -> None:
        """Hand each new inbound message to the conversation layer, after the
        webhook's own work is durably committed. Isolated from ingestion: an
        error here is logged, never surfaced as a non-200.

        ``celery`` dispatch (production, build-plan §3): enqueue one turn task
        per lead — the task applies the debounce window + per-lead lock. The
        webhook returns 200 to Meta as soon as the enqueue is done.

        ``inline`` dispatch (dev / tests / ``leadbot simulate``): run the engine
        here, one message at a time.
        """

        if not self._pending_conversations or not self._conversation_enabled:
            return

        if self._settings.webhook_conversation_dispatch == "celery":
            self._enqueue_pending_turns()
            self._pending_conversations.clear()
            return

        from app.services.conversation.engine import ConversationEngine

        engine = ConversationEngine(
            self._session,
            self._redis,
            self._settings,
            llm=self._llm,
            kb=self._kb,
            wa_client=self._wa_client,
        )
        for lead_id, message_id in self._pending_conversations:
            try:
                lead = await self._session.get(Lead, lead_id)
                message = await self._session.get(Message, message_id)
                if lead is None or message is None:  # pragma: no cover - defensive
                    continue
                await engine.handle_inbound(lead, message)
            except Exception:  # noqa: BLE001
                logger.exception("conversation engine crashed for lead %s", lead_id)
                await self._session.rollback()
        self._pending_conversations.clear()

    def _enqueue_pending_turns(self) -> None:
        seen: set[uuid.UUID] = set()
        try:
            from app.scheduler.conversation_tasks import process_lead_turn
        except Exception:  # noqa: BLE001 - Celery not importable -> don't fail ingestion
            logger.exception("could not import turn task; conversation skipped")
            return
        for lead_id, _ in self._pending_conversations:
            if lead_id in seen:
                continue
            seen.add(lead_id)
            try:
                process_lead_turn.delay(str(lead_id))
            except Exception:  # noqa: BLE001 - broker down -> log, never 500 the webhook
                logger.exception("failed to enqueue turn for lead %s", lead_id)

    # -- helpers --------------------------------------------------------
    async def _create_event(
        self,
        event_hash: str,
        signature_valid: bool,
        raw_body: bytes,
        headers: dict[str, str],
        source_ip: str | None,
    ) -> WebhookEvent:
        event = WebhookEvent(
            event_hash=event_hash,
            signature_valid=signature_valid,
            source_ip=source_ip,
            raw_text=raw_body.decode("utf-8", errors="replace"),
            headers=_redact_headers(headers),
        )
        self._session.add(event)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._session.scalar(
                select(WebhookEvent).where(WebhookEvent.event_hash == event_hash)
            )
            assert existing is not None
            return existing
        return event

    async def _record_failure(self, event_hash: str, error: str) -> None:
        event = await self._session.scalar(
            select(WebhookEvent).where(WebhookEvent.event_hash == event_hash)
        )
        if event is None:  # pragma: no cover - defensive
            return
        event.processed = False
        event.processed_at = None
        event.processing_error = error[:4000]
        await self._session.commit()

    async def _process(
        self, envelope: WebhookEnvelope, event: WebhookEvent
    ) -> tuple[int, int]:
        msg_count = 0
        status_count = 0
        for entry in envelope.entry:
            for change in entry.changes:
                if change.field and change.field != "messages":
                    logger.info("ignoring webhook change field %s", change.field)
                    continue
                value = change.value
                for message in value.messages or []:
                    await self._handle_inbound(value, message, event)
                    msg_count += 1
                for status in value.statuses or []:
                    await self._handle_status(status, event)
                    status_count += 1
        return msg_count, status_count

    async def _handle_inbound(
        self, value: ChangeValue, message: InboundMessage, event: WebhookEvent
    ) -> None:
        if not message.from_:
            logger.warning("inbound message %s has no sender; skipping", message.id)
            return
        try:
            sender = normalize_wa_id(message.from_, self._settings.default_phone_region)
        except PhoneNormalizationError:
            logger.warning("inbound message %s has unparseable sender; skipping", message.id)
            return

        lead, created = await leads_service.find_or_create(
            self._session, sender, source="whatsapp_inbound"
        )

        display_to = value.metadata.display_phone_number if value.metadata else None
        text = _extract_text(message)
        message_type = _MESSAGE_TYPE_MAP.get(message.type or "", MessageType.UNKNOWN)
        ts = from_unix(message.timestamp)

        persisted, is_new = await messages_service.persist_inbound(
            self._session,
            lead,
            wa_message_id=message.id,
            message_type=message_type,
            body=text,
            from_phone=sender.e164,
            to_phone=display_to,
            wa_timestamp=ts,
            raw_payload=message.model_dump(mode="json"),
            webhook_event_id=event.id,
        )
        if not is_new:
            logger.info("inbound %s already processed; skipping", message.id)
            return

        # capture the WhatsApp profile name if we don't have one
        if not lead.full_name and value.contacts:
            for contact in value.contacts:
                if contact.profile and contact.profile.name:
                    lead.full_name = contact.profile.name
                    break

        # --- STOP / opt-out short-circuit (BEFORE normal processing) -----
        if text and is_opt_out(text, self._stop_keywords):
            await consent_service.opt_out(
                self._session,
                lead,
                reason_text=text,
                inbound_message=persisted,
                actor="lead",
            )
            await self._windows.close(lead)
            logger.info(
                "opt-out recorded",
                extra=log_extra(phone=mask_phone(lead.phone_e164), wamid=message.id),
            )
            return

        if lead.lifecycle_state == LifecycleState.OPTED_OUT:
            # Opted-out lead sent a normal message; record it, do not re-engage.
            logger.info(
                "message from opted-out lead; persisted, not re-engaged",
                extra=log_extra(phone=mask_phone(lead.phone_e164)),
            )
            return

        # --- normal processing ---------------------------------------
        # Window is measured from when we processed the inbound message (robust
        # to clock skew and to replayed/stale payloads), not the payload's own
        # timestamp.
        await self._windows.touch(lead)
        try:
            await state_machine.apply_event(
                self._session,
                lead,
                LifecycleEvent.INBOUND_MESSAGE,
                actor="lead",
                reason="inbound message",
                context={"wamid": message.id, "created_lead": created},
            )
        except Exception:  # noqa: BLE001
            logger.exception("state transition failed for inbound %s", message.id)
            raise

        # Queue for the conversation engine (runs post-commit).
        if self._conversation_enabled and text:
            self._pending_conversations.append((lead.id, persisted.id))

    async def _handle_status(self, status: StatusUpdate, event: WebhookEvent) -> None:
        if not status.recipient_id:
            logger.warning("status %s has no recipient_id; skipping", status.id)
            return
        mapped = _STATUS_MAP.get((status.status or "").lower())
        if mapped is None:
            logger.info("unknown status %r for %s; skipped", status.status, status.id)
            return

        await messages_service.apply_status_update(
            self._session,
            self._settings,
            wa_message_id=status.id,
            status=mapped,
            wa_timestamp=from_unix(status.timestamp),
            recipient_wa_id=status.recipient_id,
            conversation_id=status.conversation.id if status.conversation else None,
            pricing=status.pricing.model_dump() if status.pricing else None,
            errors=status.errors,
            raw_payload=status.model_dump(mode="json"),
            webhook_event_id=event.id,
        )
