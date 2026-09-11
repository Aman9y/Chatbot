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
        "COST FIGURES ARE ANSWER-ONLY. Never volunteer a number, a range, or the "
        "fees topic. Quote a figure ONLY when the lead has actually asked about "
        "cost — for a specific country, or about cost/budget in general. On an "
        "opening or generic message (\"I want to do MBBS abroad\", \"tell me "
        "about the process\", a greeting) do NOT mention any figure or name a "
        "country — ask what they're looking for instead.",
        "",
        "When they HAVE asked about cost, you may quote these approved per-country "
        "ranges, and ONLY these — each range belongs to its country, never quote "
        "one country's range for another, and never anything more precise than "
        "the range:",
        "  " + " | ".join(normal),
    ]
    for c in settings.sensitive_cost_country_list:
        if c in ranges:
            lines.append(f"  {c}: {ranges[c]} — HIGHER-COST, special handling below")

    phone = settings.counselor_phone.strip()
    lines += [
        "",
        "Rules for every cost reply:",
        "- Normally answer ONE country's cost per message. The one exception: "
        "Uzbekistan, Kazakhstan and Kyrgyzstan genuinely share the same range, so "
        "you may cite them together as examples of ONE shared figure (see "
        "'abroad average' below) — that is not blending, it's one real number "
        "for an equivalent tier. Never do this across countries with different "
        "ranges.",
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
        "- Note that the real, personalised number needs a conversation — but only "
        "add the call as a next step if the per-turn CTA guidance allows it.",
        "",
        "The 'abroad average': when giving a general cost picture rather than "
        "one specific country's figure, the ~₹30–35 lakh range with Uzbekistan, "
        "Kazakhstan and Kyrgyzstan as the examples IS the real, confirmed "
        "abroad-average figure — state it with confidence, not hedged.",
        "",
        "Russia and Georgia in a 'broader options' pitch: when you're naming "
        "several countries as options (not answering a direct question about "
        "Russia or Georgia specifically), do NOT fold their cost into the "
        "₹30–35 lakh figure and do NOT state a number for them in that breath — "
        "say they have their own separate pricing and offer to share it if the "
        "lead wants. The moment they ask about Russia's or Georgia's actual cost "
        "directly, the normal approved-range rules below apply exactly as "
        "written (Russia's own range, and Georgia's mandatory reason + number).",
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
            "No direct number is configured. Keep it to 'a quick call with "
            f"{name} is the best next step' — never promise that he will call "
            "or contact them."
        )
    return (
        f"Give {name}'s direct number, {phone}, in these situations: (a) any "
        "Georgia or Nepal cost reply; (b) when a lead explicitly asks to talk to "
        "someone / for a contact number; (c) when a lead has agreed to a call or "
        "asked how to reach him. Always frame it as THEM calling or messaging him "
        f"(\"you can call {name} on {phone} whenever suits you\") — never as him "
        "calling, contacting, or reaching out to them. There is no booking link "
        "and no calendar; the lead makes the contact."
    )


def _parent_join_speaker_clause(settings: Settings) -> str:
    # Director review: deactivate the parent-join prompts from the live flow
    # but keep the code path intact behind `parent_prompt_enabled` — a config
    # flip, not a code change, brings it back.
    if not settings.parent_prompt_enabled:
        return ""
    return " Offer to have them on the call with their child."


def _parent_join_booking_clause(settings: Settings) -> str:
    if not settings.parent_prompt_enabled:
        return ""
    return '- Offer to include a parent: "Would you like your parent on the call too?"\n'


def render_system_prompt(settings: Settings) -> str:
    company = settings.company_name.strip() or "our team"
    counselor = settings.counselor_name.strip() or "our counsellor"
    office = settings.office_address.strip() or "our office (address shared on request)"
    phone = settings.counselor_phone.strip()
    bot_name = settings.bot_name.strip() or "the assistant"
    maps_link_clause = f"and map link {settings.maps_link}" if settings.maps_link.strip() else ""

    replacements = {
        "{{company_name}}": company,
        "{{bot_name}}": bot_name,
        "{{bot_pronoun_subject}}": settings.bot_pronoun_subject.strip() or "it",
        "{{bot_pronoun_possessive}}": settings.bot_pronoun_possessive.strip() or "its",
        "{{counselor_name}}": counselor,
        "{{counselor_phone}}": phone or "(no number configured)",
        "{{languages}}": ", ".join(settings.language_list),
        "{{premium_countries}}": ", ".join(settings.premium_cost_country_list),
        "{{cost_clause}}": _cost_clause(settings),
        "{{india_compare_clause}}": _india_compare_clause(settings),
        "{{neet_cutoff_clause}}": _neet_cutoff_clause(settings),
        "{{about_clause}}": _about_clause(settings),
        "{{contact_clause}}": _contact_clause(settings),
        "{{deflection_modes}}": modes_reference(bot_name),
        "{{office_address}}": office,
        "{{maps_link_clause}}": maps_link_clause,
        "{{parent_join_speaker_clause}}": _parent_join_speaker_clause(settings),
        "{{parent_join_booking_clause}}": _parent_join_booking_clause(settings),
    }
    text = _template()
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text
