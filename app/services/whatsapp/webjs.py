"""WebJS WhatsApp client — HTTP bridge to the whatsapp-web.js Node service.

IMPORTANT:
    whatsapp-web.js is an UNOFFICIAL WhatsApp Web automation library.  It is
    NOT endorsed or approved by Meta/WhatsApp and carries a real risk of
    account banning.  This client is a TEMPORARY bridge intended to be replaced
    by the primary 360dialog integration once the Meta onboarding period ends.

This client implements the :class:`WhatsAppClient` ABC by forwarding every
outbound send to the companion Node.js service (``webjs-service/server.js``)
via an authenticated internal HTTP ``POST /send`` request.

The upstream application (``OutreachService``, ``ConversationEngine``) does
NOT need to know it is talking to WebJS instead of 360dialog or Meta — the
interface is identical.

Configuration (all read from :class:`~app.config.Settings`):
    WEBJS_SERVICE_URL   — internal URL of the Node service (e.g. http://webjs:3001)
    WEBJS_API_SECRET    — shared secret; must match the Node service's value

Behaviour:
    * ``send_text``     — forwards the message to POST /send
    * ``send_template`` — rendered as a plain text message (webjs has no
                          official template API; the template_name is logged
                          so the operator knows which template was invoked)
    * ``mark_read``     — no-op (not exposed over the HTTP bridge; cosmetic)
    * ``aclose``        — closes the httpx client
    * 429 from Node     — recipient cap reached → raises ``WhatsAppAPIError``
    * 503 from Node     — WA client not ready  → raises ``WhatsAppAPIError``
"""

from __future__ import annotations

import httpx

from app.config import Settings
from app.errors import WhatsAppAPIError
from app.logging_config import get_logger, mask_phone
from app.services.whatsapp.base import SendResult, WhatsAppClient

logger = get_logger(__name__)


class WebJSWhatsAppClient(WhatsAppClient):
    """Drop-in ``WhatsAppClient`` implementation backed by the webjs-service."""

    name = "webjs"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        if not settings.webjs_api_secret:
            raise RuntimeError(
                "WHATSAPP_CLIENT=webjs requires WEBJS_API_SECRET to be set."
            )
        if not settings.webjs_service_url:
            raise RuntimeError(
                "WHATSAPP_CLIENT=webjs requires WEBJS_SERVICE_URL to be set."
            )
        self._settings = settings
        self._send_url = f"{settings.webjs_service_url.rstrip('/')}/send"
        self._client = client or httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            headers={"Authorization": f"Bearer {settings.webjs_api_secret}"},
        )

    # ── internal helpers ──────────────────────────────────────────────────────

    async def _post_send(self, phone: str, message: str, msg_type: str) -> dict:
        """POST to the Node /send endpoint and return the parsed JSON body."""
        payload = {"phone": phone.lstrip("+"), "message": message, "type": msg_type}
        try:
            response = await self._client.post(self._send_url, json=payload)
        except httpx.RequestError as exc:
            raise WhatsAppAPIError(
                status=0,
                body=f"webjs-service unreachable: {exc}",
                payload=payload,
            ) from exc

        data: dict = {}
        try:
            data = response.json()
        except Exception:  # noqa: BLE001
            data = {"raw": response.text}

        if response.status_code == 429:
            used = data.get("recipients_used", "?")
            cap  = data.get("recipients_max", "?")
            logger.warning(
                "webjs recipient cap reached (%s/%s) — send rejected for %s",
                used, cap, mask_phone(phone),
            )
            raise WhatsAppAPIError(
                status=429,
                body=f"webjs recipient cap reached ({used}/{cap})",
                payload=payload,
            )

        if response.status_code == 503:
            wa_state = data.get("wa_state", "unknown")
            hint     = data.get("hint", "")
            logger.error(
                "webjs-service not ready (wa_state=%s): %s", wa_state, hint
            )
            raise WhatsAppAPIError(
                status=503,
                body=f"webjs client not ready (state={wa_state}): {hint}",
                payload=payload,
            )

        if response.status_code >= 400:
            error = data.get("error", response.text[:200])
            logger.error(
                "webjs send failed (%d) for %s: %s",
                response.status_code, mask_phone(phone), error,
            )
            raise WhatsAppAPIError(
                status=response.status_code,
                body=error,
                payload=payload,
            )

        return data

    # ── WhatsAppClient ABC ────────────────────────────────────────────────────

    async def send_text(
        self, *, to: str, text: str, preview_url: bool = False
    ) -> SendResult:
        data = await self._post_send(to, text, "text")
        message_id = data.get("message_id", f"webjs_{to}")
        logger.info("webjs send_text ok to=%s msg_id=%s", mask_phone(to), message_id)
        return SendResult(
            message_id=message_id,
            raw={
                "messages": [{"id": message_id}],
                "recipients_used": data.get("recipients_used"),
                "recipients_max": data.get("recipients_max"),
            },
        )

    async def send_template(
        self,
        *,
        to: str,
        template_name: str,
        language: str,
        variables: dict[str, list[str]] | None = None,
    ) -> SendResult:
        """Send a template as a plain text message.

        whatsapp-web.js does not have access to Meta's official template API,
        so templates cannot be sent as proper WhatsApp Template Messages.
        Instead, the template_name is logged and the message text is sent as
        a regular free-text message.

        The caller (OutreachService / scheduler sweeps) should be aware that:
          * This does NOT count as a Business-Initiated template send.
          * It consumes the 24h service window, the same as any free-text send.
          * Meta's template approval has no effect here.

        The ``message`` field should be pre-rendered by the caller.  If
        ``variables`` contains a ``body`` key, the first element is used as
        the message body; otherwise template_name is sent as a plain label.
        """
        # Best-effort message body: use the first body variable if supplied,
        # or fall back to a labelled placeholder the operator can read.
        body_vars = (variables or {}).get("body") or []
        if body_vars:
            text = " ".join(str(v) for v in body_vars)
        elif template_name == self._settings.consent_ask_template_name:
            text = self._settings.opening_message
        else:
            text = f"[{template_name}]"

        logger.info(
            "webjs send_template (as text) to=%s template=%s lang=%s",
            mask_phone(to), template_name, language,
        )
        return await self.send_text(to=to, text=text)

    async def mark_read(self, *, message_id: str) -> None:
        # mark_read is not exposed over the webjs HTTP bridge.
        # This is cosmetic — it does not affect any business logic.
        logger.debug("webjs mark_read no-op for msg_id=%s", message_id)

    async def aclose(self) -> None:
        await self._client.aclose()
