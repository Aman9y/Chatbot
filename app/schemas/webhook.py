"""Pydantic models for the Meta WhatsApp webhook payload.

All models allow extra fields (``extra="allow"``) so a new Meta field never
breaks ingestion — we persist the raw body regardless.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_cfg = ConfigDict(extra="allow", populate_by_name=True)


class TextBody(BaseModel):
    model_config = _cfg
    body: str | None = None


class ButtonBody(BaseModel):
    model_config = _cfg
    text: str | None = None
    payload: str | None = None


class InteractiveReply(BaseModel):
    model_config = _cfg
    id: str | None = None
    title: str | None = None


class Interactive(BaseModel):
    model_config = _cfg
    type: str | None = None
    button_reply: InteractiveReply | None = None
    list_reply: InteractiveReply | None = None


class Profile(BaseModel):
    model_config = _cfg
    name: str | None = None


class Contact(BaseModel):
    model_config = _cfg
    wa_id: str | None = None
    profile: Profile | None = None


class InboundMessage(BaseModel):
    model_config = _cfg
    id: str
    from_: str | None = Field(default=None, alias="from")
    timestamp: str | None = None
    type: str | None = None
    text: TextBody | None = None
    button: ButtonBody | None = None
    interactive: Interactive | None = None
    context: dict[str, Any] | None = None
    errors: list[dict[str, Any]] | None = None


class ConversationOrigin(BaseModel):
    model_config = _cfg
    type: str | None = None


class Conversation(BaseModel):
    model_config = _cfg
    id: str | None = None
    origin: ConversationOrigin | None = None


class Pricing(BaseModel):
    model_config = _cfg
    billable: bool | None = None
    pricing_model: str | None = None
    category: str | None = None
    type: str | None = None


class StatusUpdate(BaseModel):
    model_config = _cfg
    id: str
    status: str | None = None
    timestamp: str | None = None
    recipient_id: str | None = None
    conversation: Conversation | None = None
    pricing: Pricing | None = None
    errors: list[dict[str, Any]] | None = None


class Metadata(BaseModel):
    model_config = _cfg
    display_phone_number: str | None = None
    phone_number_id: str | None = None


class ChangeValue(BaseModel):
    model_config = _cfg
    messaging_product: str | None = None
    metadata: Metadata | None = None
    contacts: list[Contact] | None = None
    messages: list[InboundMessage] | None = None
    statuses: list[StatusUpdate] | None = None
    errors: list[dict[str, Any]] | None = None


class Change(BaseModel):
    model_config = _cfg
    field: str | None = None
    value: ChangeValue = Field(default_factory=ChangeValue)


class Entry(BaseModel):
    model_config = _cfg
    id: str | None = None
    changes: list[Change] = Field(default_factory=list)


class WebhookEnvelope(BaseModel):
    model_config = _cfg
    object: str | None = None
    entry: list[Entry] = Field(default_factory=list)
