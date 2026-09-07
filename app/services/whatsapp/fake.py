from __future__ import annotations

from typing import Any

from app.errors import WhatsAppAPIError
from app.services.whatsapp.base import SendResult, WhatsAppClient


class FakeWhatsAppClient(WhatsAppClient):
    """Deterministic in-memory client for tests and offline development.

    Records every send in ``self.sent``; produces stable ``wamid.FAKE######``
    ids so tests can assert on them. Add a recipient to ``fail_on`` to simulate
    an API failure for that number.
    """

    name = "fake"

    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.reads: list[str] = []
        self.fail_on: set[str] = set(fail_on or set())
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"wamid.FAKE{self._counter:06d}"

    def _guard(self, to: str) -> None:
        if to in self.fail_on or to.lstrip("+") in self.fail_on:
            raise WhatsAppAPIError(status=400, body=f"fake failure for {to}", payload={"to": to})

    async def send_text(
        self, *, to: str, text: str, preview_url: bool = False
    ) -> SendResult:
        self._guard(to)
        mid = self._next_id()
        record = {"kind": "text", "to": to, "text": text, "message_id": mid}
        self.sent.append(record)
        return SendResult(
            message_id=mid,
            raw={"messages": [{"id": mid}], "contacts": [{"wa_id": to.lstrip("+")}]},
        )

    async def send_template(
        self,
        *,
        to: str,
        template_name: str,
        language: str,
        variables: dict[str, list[str]] | None = None,
    ) -> SendResult:
        self._guard(to)
        mid = self._next_id()
        record = {
            "kind": "template",
            "to": to,
            "template_name": template_name,
            "language": language,
            "variables": variables or {},
            "message_id": mid,
        }
        self.sent.append(record)
        return SendResult(message_id=mid, raw={"messages": [{"id": mid}]})

    async def mark_read(self, *, message_id: str) -> None:
        self.reads.append(message_id)

    def last(self) -> dict[str, Any] | None:
        return self.sent[-1] if self.sent else None
