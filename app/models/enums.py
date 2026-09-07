"""Enumerations used across models and services.

All values are lowercase snake_case strings; these are what get persisted
(``Enum(..., values_callable=...)`` in :mod:`app.models.common`).
"""

from __future__ import annotations

from enum import StrEnum


class LifecycleState(StrEnum):
    """The single operational state machine (plan §5, critique A5).

    Funnel stage and lead score are *derived* from this — see ``Lead.funnel_stage``.
    """

    NEVER_CONTACTED = "never_contacted"
    CONTACTED = "contacted"
    ENGAGED = "engaged"
    SILENT = "silent"
    NURTURE = "nurture"
    DORMANT = "dormant"
    HANDOFF = "handoff"
    OPTED_OUT = "opted_out"


class LifecycleEvent(StrEnum):
    OUTBOUND_TEMPLATE_SENT = "outbound_template_sent"
    OUTBOUND_MESSAGE_SENT = "outbound_message_sent"
    INBOUND_MESSAGE = "inbound_message"
    BOOKING_CONFIRMED = "booking_confirmed"
    HUMAN_TAKEOVER = "human_takeover"
    HUMAN_RELEASE = "human_release"
    SERVICE_WINDOW_EXPIRED = "service_window_expired"
    NURTURE_TIMEOUT = "nurture_timeout"
    RETRY_ROUNDS_EXHAUSTED = "retry_rounds_exhausted"
    OPT_OUT = "opt_out"


class FunnelStage(StrEnum):
    LEAD = "lead"
    CONTACTED = "contacted"
    ENGAGED = "engaged"
    QUALIFIED = "qualified"
    BOOKED = "booked"
    HANDED_OFF = "handed_off"
    LOST = "lost"


class RoleHint(StrEnum):
    STUDENT = "student"
    PARENT = "parent"
    UNKNOWN = "unknown"


class InterestTemperature(StrEnum):
    HOT = "hot"
    MID = "mid"
    COLD = "cold"
    UNKNOWN = "unknown"


class MinorStatus(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class MinorPolicyStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    PENDING_REVIEW = "pending_review"
    CLEARED_PARENT_CONSENT = "cleared_parent_consent"
    BLOCKED = "blocked"


class NeetCategory(StrEnum):
    GENERAL = "general"
    OBC = "obc"
    SC = "sc"
    ST = "st"
    EWS = "ews"
    UNKNOWN = "unknown"


class EligibilityFlag(StrEnum):
    ABOVE_CUTOFF = "above_cutoff"
    BELOW_CUTOFF = "below_cutoff"
    UNKNOWN = "unknown"


class ConsentStatus(StrEnum):
    UNKNOWN = "unknown"
    OPTED_IN = "opted_in"
    OPTED_OUT = "opted_out"
    WITHDRAWN = "withdrawn"


class ConsentMethod(StrEnum):
    IMPORTED_CSV_ASSERTION = "imported_csv_assertion"
    WEB_FORM = "web_form"
    INBOUND_STOP_KEYWORD = "inbound_stop_keyword"
    INBOUND_OPT_IN_KEYWORD = "inbound_opt_in_keyword"
    MANUAL_ENTRY = "manual_entry"
    API = "api"


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageType(StrEnum):
    TEXT = "text"
    TEMPLATE = "template"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    STICKER = "sticker"
    LOCATION = "location"
    CONTACTS = "contacts"
    INTERACTIVE = "interactive"
    BUTTON = "button"
    REACTION = "reaction"
    ORDER = "order"
    SYSTEM = "system"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class MessageStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    RECEIVED = "received"
    DELETED = "deleted"


class TemplateCategory(StrEnum):
    MARKETING = "marketing"
    UTILITY = "utility"
    AUTHENTICATION = "authentication"
    UNKNOWN = "unknown"


class SentBy(StrEnum):
    BOT = "bot"
    HUMAN = "human"
    SYSTEM = "system"
    LEAD = "lead"


# Ordering used to reconcile out-of-order status webhooks. A status only
# "advances" a message if its rank is strictly greater than the current one.
STATUS_RANK: dict[MessageStatus, int] = {
    MessageStatus.QUEUED: 0,
    MessageStatus.RECEIVED: 1,
    MessageStatus.SENT: 1,
    MessageStatus.DELIVERED: 2,
    MessageStatus.READ: 3,
    MessageStatus.DELETED: 4,
    MessageStatus.FAILED: 5,
}
