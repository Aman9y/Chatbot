from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings
from app.errors import WhatsAppAPIError
from app.logging_config import get_logger
from app.services.whatsapp.base import SendResult, WhatsAppClient

logger = get_logger(__name__)


def _components_from_variables(
    variables: dict[str, list[str]] | None,
) -> list[dict[str, Any]]:
    """Turn {"body": ["Amit", "call"], "header": ["MBBS"]} into Cloud API
    template components."""

    if not variables:
        return []
    components: list[dict[str, Any]] = []
    for kind in ("header", "body"):
        values = variables.get(kind)
        if values:
            components.append(
                {
                    "type": kind,
                    "parameters": [{"type": "text", "text": str(v)} for v in values],
                }
            )
    for idx, values in enumerate(variables.get("buttons", []) or []):
        components.append(
            {
                "type": "button",
                "sub_type": "url",
                "index": str(idx),
                "parameters": [{"type": "text", "text": str(values)}],
            }
        )
    return components


class MetaWhatsAppClient(WhatsAppClient):
    name = "meta"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        if not settings.meta_access_token or not settings.meta_phone_number_id:
            raise RuntimeError(
                "WHATSAPP_CLIENT=meta requires META_ACCESS_TOKEN and META_PHONE_NUMBER_ID"
            )
        self._settings = settings
        self._url = (
            f"{settings.meta_graph_base_url.rstrip('/')}/"
            f"{settings.meta_graph_version}/{settings.meta_phone_number_id}/messages"
        )
        self._client = client or httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            headers={"Authorization": f"Bearer {settings.meta_access_token}"},
        )

    @staticmethod
    def _to(recipient: str) -> str:
        return recipient.lstrip("+")

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(self._url, json=payload)
        if response.status_code >= 400:
            logger.error("meta send failed: %s %s", response.status_code, response.text[:500])
            raise WhatsAppAPIError(
                status=response.status_code, body=response.text, payload=payload
            )
        return response.json()

    async def send_text(
        self, *, to: str, text: str, preview_url: bool = False
    ) -> SendResult:
        data = await self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": self._to(to),
                "type": "text",
                "text": {"body": text, "preview_url": preview_url},
            }
        )
        return SendResult(message_id=data["messages"][0]["id"], raw=data)

    async def send_template(
        self,
        *,
        to: str,
        template_name: str,
        language: str,
        variables: dict[str, list[str]] | None = None,
    ) -> SendResult:
        template: dict[str, Any] = {
            "name": template_name,
            "language": {"code": language},
        }
        components = _components_from_variables(variables)
        if components:
            template["components"] = components
        data = await self._post(
            {
                "messaging_product": "whatsapp",
                "to": self._to(to),
                "type": "template",
                "template": template,
            }
        )
        return SendResult(message_id=data["messages"][0]["id"], raw=data)

    async def mark_read(self, *, message_id: str) -> None:
        await self._post(
            {
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": message_id,
            }
        )

    async def aclose(self) -> None:
        await self._client.aclose()
