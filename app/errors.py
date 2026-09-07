"""Domain exceptions."""

from __future__ import annotations


class LeadBotError(Exception):
    """Base class for all application-level errors."""


class PhoneNormalizationError(LeadBotError, ValueError):
    """Raised when a phone number cannot be parsed into a valid E.164 number."""


class IllegalTransition(LeadBotError):
    """Raised when a lifecycle state + event pair has no defined transition."""

    def __init__(self, current: object, event: object) -> None:
        self.current = current
        self.event = event
        super().__init__(f"no transition from {current!r} on event {event!r}")


class OutreachBlocked(LeadBotError):
    """Raised when the outreach guard refuses a bot-initiated message."""

    def __init__(self, decision: object) -> None:
        self.decision = decision
        reasons = getattr(decision, "hard_blocks", [])
        super().__init__(f"outreach blocked: {', '.join(reasons) or 'unknown'}")


class OptOutViolation(LeadBotError):
    """Raised if code attempts to persist an outbound message to an opted-out lead."""

    def __init__(self, lead_id: object) -> None:
        self.lead_id = lead_id
        super().__init__(f"attempt to contact opted-out lead {lead_id}")


class ServiceWindowClosed(LeadBotError):
    """Raised when a free-form bot message is attempted outside the 24h window."""

    def __init__(self, lead_id: object) -> None:
        self.lead_id = lead_id
        super().__init__(f"service window closed for lead {lead_id}")


class WhatsAppAPIError(LeadBotError):
    """Raised when the Meta Graph API returns an error response."""

    def __init__(self, *, status: int, body: str, payload: dict | None = None) -> None:
        self.status = status
        self.body = body
        self.payload = payload
        super().__init__(f"WhatsApp API error {status}: {body[:500]}")
