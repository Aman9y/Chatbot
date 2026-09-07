"""Model registry — importing this module ensures all mappers are configured."""

from app.db.base import Base
from app.models.consent import ConsentRecord
from app.models.household import Household
from app.models.lead import Lead
from app.models.lifecycle import LifecycleTransition
from app.models.message import Message
from app.models.webhook_event import WebhookEvent

__all__ = [
    "Base",
    "ConsentRecord",
    "Household",
    "Lead",
    "LifecycleTransition",
    "Message",
    "WebhookEvent",
]
