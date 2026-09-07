"""NEET eligibility flag.

If the cutoffs are not configured (critique B9 — Aman has not confirmed which
NEET year / what cutoffs), this returns UNKNOWN. It never guesses.
"""

from __future__ import annotations

from app.config import Settings
from app.models.enums import EligibilityFlag, NeetCategory

_RELAXED_CATEGORIES = {
    NeetCategory.OBC,
    NeetCategory.SC,
    NeetCategory.ST,
    NeetCategory.EWS,
}


def compute_eligibility(
    score: int | None,
    category: NeetCategory,
    settings: Settings,
) -> EligibilityFlag:
    if score is None:
        return EligibilityFlag.UNKNOWN

    if category in _RELAXED_CATEGORIES:
        cutoff = settings.neet_cutoff_obc
    else:
        cutoff = settings.neet_cutoff_general

    if cutoff is None:
        return EligibilityFlag.UNKNOWN

    return EligibilityFlag.ABOVE_CUTOFF if score >= cutoff else EligibilityFlag.BELOW_CUTOFF
