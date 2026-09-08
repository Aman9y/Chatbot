"""Render the operational system prompt from app/prompts/system_prompt.md."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.config import Settings

_PROMPT_PATH = Path(__file__).resolve().parents[3] / "app" / "prompts" / "system_prompt.md"


@lru_cache
def _template() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _stateable_cost_clause(settings: Settings) -> str:
    countries = ", ".join(settings.stateable_cost_country_list) or "the economical tier"
    if settings.stateable_cost_range:
        return (
            f"For {countries} you may quote the approved range only: "
            f"{settings.stateable_cost_range}. Nothing more precise, and nothing if "
            "the knowledge snippet for that country is missing."
        )
    return (
        f"Even for {countries}, do NOT state a specific figure — the approved range "
        "is not configured yet. Say the counsellor gives current figures on the call."
    )


def _neet_cutoff_clause(settings: Settings) -> str:
    if settings.neet_cutoff_general is not None and settings.neet_cutoff_obc is not None:
        year = f" for {settings.neet_year}" if settings.neet_year else ""
        return (
            f"The NEET qualifying cutoff{year} is around {settings.neet_cutoff_general} "
            f"(general) / {settings.neet_cutoff_obc} (OBC). State it plainly only if "
            "asked; don't lead with numbers."
        )
    return (
        "Do NOT state a specific NEET cutoff number — it is not confirmed in your "
        "configuration. Say there is a qualifying cutoff each year and the counsellor "
        "confirms the exact current figure and what it means for a given score."
    )


def render_system_prompt(settings: Settings) -> str:
    company = settings.company_name.strip() or "our team"
    counselor = settings.counselor_name.strip() or "our counsellor"
    office = settings.office_address.strip() or "our office (address shared on request)"

    booking_link_clause = (
        f"Share the booking link once if useful: {settings.booking_link}"
        if settings.booking_link.strip()
        else "There is no self-serve booking link — the counsellor calls at the agreed time."
    )
    maps_link_clause = f"and map link {settings.maps_link}" if settings.maps_link.strip() else ""

    replacements = {
        "{{company_name}}": company,
        "{{counselor_name}}": counselor,
        "{{languages}}": ", ".join(settings.language_list),
        "{{premium_countries}}": ", ".join(settings.premium_cost_country_list),
        "{{stateable_cost_countries}}": ", ".join(settings.stateable_cost_country_list),
        "{{stateable_cost_clause}}": _stateable_cost_clause(settings),
        "{{neet_cutoff_clause}}": _neet_cutoff_clause(settings),
        "{{office_address}}": office,
        "{{booking_link_clause}}": booking_link_clause,
        "{{maps_link_clause}}": maps_link_clause,
    }
    text = _template()
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text
