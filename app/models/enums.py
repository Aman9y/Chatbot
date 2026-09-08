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
    # Consent/age gate parked the lead pending a human (confirmed minor, or an
    # opt-in/age answer the bot couldn't read twice). Bot stays silent.
    GATE_HOLD = "gate_hold"
    OPTED_OUT = "opted_out"


class ConsentGate(StrEnum):
    """Pre-conversation opt-in + age gate (build-plan §2 / DPDP).

    A precondition the lead must pass before ANY sales/qualification turn runs —
    NOT a second lifecycle machine. Only forward progress + terminal outcomes.
    """

    PENDING_OPT_IN = "pending_opt_in"   # consent ask owed / sent, awaiting a yes/no
    PENDING_AGE = "pending_age"         # opted in, awaiting the 18+/under-18 answer
    CLEARED = "cleared"                 # opted in + confirmed 18+ -> normal flow
    REFUSED = "refused"                 # said no -> also OPTED_OUT
    MINOR_HOLD = "minor_hold"           # confirmed under 18 -> held pending guidance
    NEEDS_HUMAN = "needs_human"         # unreadable answer twice -> human review
    NOT_REQUIRED = "not_required"       # gate disabled / not applicable (dev, placeholders)


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
    GATE_HELD = "gate_held"  # consent/age gate parked the lead for a human
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


class LeadScore(StrEnum):
    """Counsellor-prioritisation signal (plan §3 lead-scoring pipeline).

    Derived each turn from qualification completeness + eligibility + intent +
    interest temperature. Not a lifecycle state — it only orders the counsellor
    queue and gates the HIGH-intent CTA notification.
    """

    UNKNOWN = "unknown"
    LOW = "low"
    NURTURE = "nurture"
    HIGH = "high"


class LeadUrgency(StrEnum):
    THIS_INTAKE = "this_intake"
    NEXT_INTAKE = "next_intake"
    UNDECIDED = "undecided"
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
    CONVERSATIONAL_GATE = "conversational_gate"  # replied to the in-chat opt-in ask
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


class HandoffTrigger(StrEnum):
    BOOKING = "booking"
    PHASE_HANDOFF = "phase_handoff"
    HIGH_INTENT = "high_intent"
    MINOR_HOLD = "minor_hold"          # confirmed under-18 in the age gate
    CONSENT_REVIEW = "consent_review"  # opt-in/age answer unreadable twice
    GUARD_FALLBACK = "guard_fallback"
    ENGINE_ERROR = "engine_error"
    MANUAL = "manual"


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
