"""Lead scoring pipeline (build-plan §3 parallel pipeline; critique A5).

Two derived signals, recomputed every turn and written onto the lead:

  * ``interest_temperature`` (hot / mid / cold) — short-term warmth from the
    conversation dynamics (reply speed, burst size, explicit intent).
  * ``lead_score`` (LOW / NURTURE / HIGH) — overall counsellor-queue priority:
    qualification completeness + eligibility + intent + temperature.

Neither is a lifecycle state. HIGH gates a one-time "hot qualified lead"
notification to the counsellor; the bot keeps working toward the booking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.enums import EligibilityFlag, InterestTemperature, LeadScore, LeadUrgency
from app.models.lead import Lead

_NOT_INTERESTED = re.compile(
    r"\b(not interested|no longer interested|don'?t want to (?:proceed|continue|do this)|"
    r"changed my mind|drop(?:ping)? the idea|no thanks?,? not|"
    r"decided against|nahi karna|mann nahi)\b",
    re.IGNORECASE,
)

_STRONG_INTENT = re.compile(
    r"\b(let'?s (?:do|book|start|go ahead)|ready to (?:start|proceed|book|pay)|"
    r"how do (?:i|we) (?:pay|start|begin|apply)|start (?:the )?process|"
    r"want to (?:enroll|admit|join)|book the call|set up the call)\b",
    re.IGNORECASE,
)

_QUALIFIER_FIELDS = (
    "neet_score",
    "neet_category",
    "city",
    "target_country",
    "budget_band",
    "urgency",
    "parent_in_loop",
)


@dataclass
class ScoreInputs:
    latest_text: str
    inbound_count: int
    minutes_since_last_bot: float | None
    booking_detected: bool
    engagement_phase: str


@dataclass
class ScoreResult:
    score: LeadScore
    temperature: InterestTemperature
    reason: str
    not_interested: bool = False


def qualifier_completeness(lead: Lead) -> int:
    filled = 0
    for f in _QUALIFIER_FIELDS:
        v = getattr(lead, f, None)
        if f == "neet_category":
            filled += int(getattr(v, "value", v) not in (None, "unknown"))
        elif f == "urgency":
            filled += int(getattr(v, "value", v) not in (None, LeadUrgency.UNKNOWN.value))
        elif f == "parent_in_loop":
            filled += int(bool(v))
        else:
            filled += int(v not in (None, ""))
    return filled


def _temperature(inp: ScoreInputs, *, not_interested: bool) -> InterestTemperature:
    if not_interested:
        return InterestTemperature.COLD
    if inp.booking_detected or _STRONG_INTENT.search(inp.latest_text):
        return InterestTemperature.HOT
    if inp.engagement_phase == "nurture":
        return InterestTemperature.COLD
    m = inp.minutes_since_last_bot
    if m is not None:
        if m <= 10 and inp.inbound_count >= 2:
            return InterestTemperature.HOT
        if m <= 90:
            return InterestTemperature.MID
        if m > 360:
            return InterestTemperature.COLD
    if inp.inbound_count >= 4:
        return InterestTemperature.MID
    return InterestTemperature.MID


def score_lead(lead: Lead, inp: ScoreInputs) -> ScoreResult:
    not_interested = bool(_NOT_INTERESTED.search(inp.latest_text))
    temp = _temperature(inp, not_interested=not_interested)
    completeness = qualifier_completeness(lead)
    elig = lead.eligibility_flag

    if not_interested:
        return ScoreResult(
            LeadScore.LOW, temp, "lead said they are not interested", not_interested=True
        )
    if inp.booking_detected:
        return ScoreResult(LeadScore.HIGH, temp, "booking agreed", not_interested=False)
    if elig == EligibilityFlag.BELOW_CUTOFF:
        return ScoreResult(
            LeadScore.LOW,
            temp,
            "below NEET cutoff — abroad route closed for the cycle",
        )

    elig_ok = elig != EligibilityFlag.BELOW_CUTOFF
    if (
        completeness >= 3
        and elig_ok
        and temp in (InterestTemperature.HOT, InterestTemperature.MID)
    ):
        return ScoreResult(
            LeadScore.HIGH,
            temp,
            f"{completeness}/7 qualifiers, {elig.value}, {temp.value}",
        )
    if temp == InterestTemperature.COLD or completeness == 0:
        return ScoreResult(
            LeadScore.LOW, temp, f"{temp.value}, {completeness}/7 qualifiers"
        )
    return ScoreResult(
        LeadScore.NURTURE, temp, f"{completeness}/7 qualifiers, {temp.value}"
    )
