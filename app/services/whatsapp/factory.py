from __future__ import annotations

from app.config import Settings
from app.services.whatsapp.base import WhatsAppClient
from app.services.whatsapp.fake import FakeWhatsAppClient
from app.services.whatsapp.meta import MetaWhatsAppClient


def build_whatsapp_client(settings: Settings) -> WhatsAppClient:
    if settings.whatsapp_client == "meta":
        return MetaWhatsAppClient(settings)
    return FakeWhatsAppClient()
