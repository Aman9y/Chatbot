"""Render the operational system prompt from app/prompts/system_prompt.md.

The static prompt is the behaviour contract (sales-playbook philosophy + the §2
non-negotiables). Per-turn guidance — pace / CTA timing / tone stage / topic
handling (combined topic matrix) / detected objection — is appended by
``app/services/conversation/context.py`` from ``pacing.py`` and ``triage.py``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.config import Settings
from app.services.conversation.deflection import modes_reference

_PROMPT_PATH = Path(__file__).resolve().parents[3] / "app" / "prompts" / "system_prompt.md"


@lru_cache
def _template() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _cost_clause(settings: Settings) -> str:
    ranges = settings.country_cost_range_display
    if not ranges:
        return (
            "No cost ranges are configured — do NOT state any figure for any "
            "country. Say costs vary by country and package and the counsellor "
            "gives current numbers on the call."
        )

    sensitive = set(settings.sensitive_cost_country_list)
    normal = [f"{c}: {r}" for c, r in ranges.items() if c not in sensitive]
    lines = [
        "You may quote these approved per-country ranges, and ONLY these — each "
        "range belongs to its country, never quote one country's range for "
        "another, and never anything more precise than the range:",
        "  " + " | ".join(normal),
    ]
    for c in settings.sensitive_cost_country_list:
        if c in ranges:
            lines.append(f"  {c}: {ranges[c]} — HIGHER-COST, special handling below")

    phone = settings.counselor_phone.strip()
    lines += [
        "",
        "Rules for every cost reply:",
        "- Answer ONE country's cost per message. If they ask about several, take "
        "the most relevant one and offer the rest on the call.",
        "- NEVER a bare number. Always pair the figure with at least one concrete "
        "thing the money covers — visa processing, passport help, travel/airline "
        "arrangements, accommodation setup, or end-to-end support on the ground. "
        "Vary which ones you name turn to turn so three country questions in a "
        "row don't produce near-identical replies.",
        "- Read the tone. A nervous \"how much??\" needs reassurance framing; a "
        "flat \"what's the Georgia range\" can be more direct.",
        "- If the lead seems budget-constrained, surface the lower-tier countries "
        "(the ₹27–35 lakh band) naturally rather than letting them leave thinking "
        "everything costs ₹80 lakh.",
        "- End toward a call — the real, personalised number needs a conversation.",
        "",
        f"Georgia and Nepal: NEVER give the figure without the reason it is higher, "
        f"and ALWAYS offer {settings.counselor_name.strip() or 'the director'}'s "
        f"number in the same reply"
        + (f" ({phone})" if phone else "")
        + ". Nepal is higher because it is very close to India (easy, cheap "
        "travel; easy to stay in touch with family), the academic structure is "
        "almost identical to India's, and many Indian professors teach there. "
        "Georgia is higher because it is a genuinely different education market "
        "with its own cost structure, not comparable to the CIS-tier countries. "
        "If the number clearly lands badly, don't pile on justification — pivot "
        "to the call.",
    ]
    return "\n".join(lines)


def _india_compare_clause(settings: Settings) -> str:
    if not settings.india_compare_cost_range:
        return (
            "When asked to compare MBBS in India vs abroad, keep it qualitative — "
            "private MBBS in India costs several times more than the economical "
            "route abroad — and let the counsellor give the actual numbers."
        )
    return (
        "When comparing MBBS in India vs abroad you may state the gap: private "
        f"MBBS in India runs roughly {settings.india_compare_cost_range}, versus "
        "the far lower economical route abroad. This is the core reason to look "
        "abroad. Never state an individual Indian college's fee, and nothing "
        "more precise than that range."
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


def _about_clause(settings: Settings) -> str:
    company = settings.company_name.strip() or "our team"
    if company == "our team":
        return (
            "If asked who you are: a consultancy that helps NEET students explore "
            "MBBS abroad. Don't invent company history or credentials."
        )
    return (
        f"{company} has over ten years of operating experience and was formally "
        "registered in 2024 — say both together, never just one. We place "
        "students only in government-led / government medical institutes in every "
        "country: this is a real differentiator and directly answers \"how do I "
        "know this university is legitimate\". Do not name a parent company. Do "
        "not invent counsellor credentials, numbers of students placed, or "
        "specific success stories."
    )


def _contact_clause(settings: Settings) -> str:
    phone = settings.counselor_phone.strip()
    name = settings.counselor_name.strip() or "our director"
    if not phone:
        return (
            "There is no self-serve number or booking link — the counsellor "
            "reaches out at the agreed time."
        )
    return (
        f"You may give {name}'s direct number, {phone}, in two situations: (a) any "
        "Georgia or Nepal cost reply, and (b) when a lead explicitly asks to talk "
        "to someone / for a contact number. Otherwise keep to the booking flow — "
        "confirm a call or office visit and say the counsellor will reach out. "
        "There is no self-serve booking link."
    )


def render_system_prompt(settings: Settings) -> str:
    company = settings.company_name.strip() or "our team"
    counselor = settings.counselor_name.strip() or "our counsellor"
    office = settings.office_address.strip() or "our office (address shared on request)"
    phone = settings.counselor_phone.strip()
    maps_link_clause = f"and map link {settings.maps_link}" if settings.maps_link.strip() else ""

    replacements = {
        "{{company_name}}": company,
        "{{counselor_name}}": counselor,
        "{{counselor_phone}}": phone or "(no number configured)",
        "{{languages}}": ", ".join(settings.language_list),
        "{{premium_countries}}": ", ".join(settings.premium_cost_country_list),
        "{{cost_clause}}": _cost_clause(settings),
        "{{india_compare_clause}}": _india_compare_clause(settings),
        "{{neet_cutoff_clause}}": _neet_cutoff_clause(settings),
        "{{about_clause}}": _about_clause(settings),
        "{{contact_clause}}": _contact_clause(settings),
        "{{deflection_modes}}": modes_reference(),
        "{{office_address}}": office,
        "{{maps_link_clause}}": maps_link_clause,
    }
    text = _template()
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text
