"""NEET eligibility flag — a pure function of (score, category, configured cutoffs).

Recomputed every time the score or category changes, so it can never drift from
its inputs and order of statement does not matter.

If the cutoffs are not configured (critique B9 — Aman has not confirmed which
NEET year / what cutoffs) this returns UNKNOWN. It never guesses.

The relaxed (OBC/SC/ST/EWS/RESERVED) cutoff is lower than the general one, so a
score that lands *between* the two bounds with an unknown category is genuinely
undetermined — that is NEEDS_CATEGORY, a state the conversation layer keeps open
and keeps asking about until the lead answers.
"""

from __future__ import annotations

from app.config import Settings
from app.models.enums import EligibilityFlag, NeetCategory

_RELAXED_CATEGORIES = frozenset(
    {
        NeetCategory.OBC,
        NeetCategory.SC,
        NeetCategory.ST,
        NeetCategory.EWS,
        NeetCategory.RESERVED,
    }
)


def category_is_known(category: NeetCategory | None) -> bool:
    """True once the lead has stated a category we can apply a cutoff to."""
    return category is not None and category is not NeetCategory.UNKNOWN


def compute_eligibility(
    score: int | None,
    category: NeetCategory | None,
    settings: Settings,
) -> EligibilityFlag:
    if score is None:
        return EligibilityFlag.UNKNOWN

    general = settings.neet_cutoff_general
    relaxed = settings.neet_cutoff_obc
    if general is None or relaxed is None:
        return EligibilityFlag.UNKNOWN

    lo, hi = min(general, relaxed), max(general, relaxed)

    if not category_is_known(category):
        # category decides it only when the score is inside the band.
        if score >= hi:
            return EligibilityFlag.ABOVE_CUTOFF
        if score < lo:
            return EligibilityFlag.BELOW_CUTOFF
        return EligibilityFlag.NEEDS_CATEGORY

    cutoff = relaxed if category in _RELAXED_CATEGORIES else general
    return (
        EligibilityFlag.ABOVE_CUTOFF if score >= cutoff else EligibilityFlag.BELOW_CUTOFF
    )
