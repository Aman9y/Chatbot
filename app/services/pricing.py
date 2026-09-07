"""Per-message cost estimation from an optional rate card.

Meta status webhooks report the pricing *category* and whether the message is
billable, but not a price. When Aman supplies a rate card (WHATSAPP_RATE_CARD),
this fills in ``cost_amount``; otherwise the cost fields stay null and only
category/billable are recorded — no invented numbers.
"""

from __future__ import annotations

from decimal import Decimal

from app.config import Settings


def estimate_cost(
    settings: Settings,
    *,
    category: str | None,
    country_iso2: str | None,
    billable: bool | None,
) -> tuple[Decimal | None, str | None]:
    if billable is False:
        return Decimal("0"), settings.rate_card_currency
    if not category:
        return None, None

    card = settings.rate_card
    if not card:
        return None, None

    country = (country_iso2 or "").upper()
    for key in (f"{category}:{country}", f"{category}:*", category):
        if key in card:
            return card[key], settings.rate_card_currency
    return None, None
