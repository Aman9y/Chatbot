from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SendResult:
    message_id: str
    raw: dict[str, Any] = field(default_factory=dict)


class WhatsAppClient(abc.ABC):
    """Transport abstraction over the WhatsApp Cloud API.

    Two implementations: :class:`MetaWhatsAppClient` (real Graph API) and
    :class:`FakeWhatsAppClient` (deterministic, in-memory, for tests + offline
    development). Swapping is a config change (``WHATSAPP_CLIENT``); no calling
    code changes.
    """

    name: str = "base"

    @abc.abstractmethod
    async def send_text(
        self, *, to: str, text: str, preview_url: bool = False
    ) -> SendResult: ...

    @abc.abstractmethod
    async def send_template(
        self,
        *,
        to: str,
        template_name: str,
        language: str,
        variables: dict[str, list[str]] | None = None,
    ) -> SendResult: ...

    @abc.abstractmethod
    async def mark_read(self, *, message_id: str) -> None: ...

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None
