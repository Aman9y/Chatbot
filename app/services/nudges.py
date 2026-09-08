"""Canned in-window nudge copy.

Nudges are pre-vetted fixed text (no LLM, no guard needed) — a light "still
there?" during the 0-24h push phase when a lead has gone quiet mid-conversation.
"""

from __future__ import annotations

from app.config import Settings

_NUDGES = [
    "Just checking in — still keen to explore MBBS abroad? Happy to set up that "
    "quick call whenever suits you.",
    "No rush at all — whenever you're ready, a short call with {counselor} is the "
    "fastest way to get clear answers for your situation.",
]


def nudge_text(settings: Settings, index: int) -> str:
    counselor = settings.counselor_name.strip() or "our counsellor"
    template = _NUDGES[min(index, len(_NUDGES) - 1)]
    return template.format(counselor=counselor)
