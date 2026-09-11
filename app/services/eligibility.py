"""NEET eligibility flag — a pure function of the two things that together
determine it, plus configured cutoffs. Recomputed every time either input
changes, so it can never drift and order of statement does not matter.

Director review (confirmed): eligibility for the abroad route needs BOTH of
these — neither alone is sufficient:
  1. NEET total score cutoff (213 General / 175 OBC) — the original check.
  2. PCB (Physics+Chemistry+Biology) percentage in the qualifying exam
     (General needs 50%, OBC needs 45%) — an equally-required second axis,
     not a minor add-on. If either one fails, the lead is not eligible,
     regardless of how well they did on the other.

If a cutoff pair is not configured (critique B9 / director not confirmed) the
corresponding axis returns "unknown" and is never used to fail or clear a
lead — it never guesses.

Both axes share the same reservation-category ambiguity: a score (or PCB%)
that sits between the general and relaxed cutoff can't be judged without
knowing the category. That is NEEDS_CATEGORY — a persistent state the
conversation layer keeps open until the lead answers (same as before PCB was
added). Once the NEET-score axis clears, if PCB% has not been stated at all
that is NEEDS_PCB — the analogous persistent state for the second axis.
"""

from __future__ import annotations

from typing import Literal

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

_Verdict = Literal["pass", "fail", "ambiguous", "unknown"]


def category_is_known(category: NeetCategory | None) -> bool:
    """True once the lead has stated a category we can apply a cutoff to."""
    return category is not None and category is not NeetCategory.UNKNOWN


def _axis_verdict(
    value: float | None,
    category: NeetCategory | None,
    general_cutoff: float | None,
    relaxed_cutoff: float | None,
) -> _Verdict:
    if value is None or general_cutoff is None or relaxed_cutoff is None:
        return "unknown"

    lo, hi = min(general_cutoff, relaxed_cutoff), max(general_cutoff, relaxed_cutoff)

    if category_is_known(category):
        cutoff = relaxed_cutoff if category in _RELAXED_CATEGORIES else general_cutoff
        return "pass" if value >= cutoff else "fail"

    # category unknown: only the two bounds that hold regardless of category
    # let us conclude anything; the band between them needs the category.
    if value >= hi:
        return "pass"
    if value < lo:
        return "fail"
    return "ambiguous"


def compute_eligibility(
    score: int | None,
    category: NeetCategory | None,
    pcb_percentage: float | None,
    settings: Settings,
) -> EligibilityFlag:
    neet = _axis_verdict(
        score, category, settings.neet_cutoff_general, settings.neet_cutoff_obc
    )
    pcb = _axis_verdict(
        pcb_percentage, category, settings.pcb_cutoff_general, settings.pcb_cutoff_obc
    )

    # Either axis failing is conclusive — no need to know the other to say
    # "not eligible" (and no need to ask more qualifying questions to get
    # there).
    if neet == "fail" or pcb == "fail":
        return EligibilityFlag.BELOW_CUTOFF

    if neet == "unknown":
        # nothing to evaluate yet — ask for the NEET score first, as before.
        return EligibilityFlag.UNKNOWN
    if neet == "ambiguous":
        # category resolves the NEET axis (and possibly PCB too) — ask that
        # before asking for PCB, so we don't collect data we may not need.
        return EligibilityFlag.NEEDS_CATEGORY

    # neet == "pass" from here.
    if pcb == "unknown":
        return EligibilityFlag.NEEDS_PCB
    if pcb == "ambiguous":
        return EligibilityFlag.NEEDS_CATEGORY

    # both axes pass (category-resolved, or both cleared the tougher bound
    # regardless of category).
    return EligibilityFlag.ABOVE_CUTOFF
