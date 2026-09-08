"""Safe canned replies used when the guard blocks a draft it cannot fix."""

from __future__ import annotations

from app.config import Settings


def safe_fallback_message(settings: Settings, *, engagement_phase: str = "push") -> str:
    counselor = settings.counselor_name.strip() or "our counselor"
    if engagement_phase == "nurture":
        return (
            f"Whenever you'd like to talk it through, {counselor} is happy to help — "
            "just say the word and I'll set up a quick call."
        )
    return (
        f"I want to make sure you get accurate answers on this, so let me set up a "
        f"short call with {counselor}. Would tomorrow work — morning or evening?"
    )
