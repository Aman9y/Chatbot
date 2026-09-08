"""Pace + conversation-depth framework (sales-playbook Part 2).

Two clocks:
  * the WhatsApp window / engagement_phase clock (push / handoff / nurture) —
    already computed on the lead;
  * the *conversation* clock — how many messages deep this exchange is, plus how
    fast the lead is replying — which is what actually drives tone and CTA
    timing.

Everything here is a pure function of inputs the engine already has. It produces
short strings that get injected into the system prompt each turn; the LLM does
the writing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Pace = Literal["constant", "moderate", "slow"]
ToneStage = Literal["curious_host", "helpful_expert", "bridge_builder", "honest_handoff"]
CtaMode = Literal["none", "soft", "direct", "honest_handoff", "nurture_soft"]

# soft-CTA / direct-CTA message-depth thresholds per pace (playbook Part 2).
_CTA_THRESHOLDS: dict[Pace, tuple[int, int]] = {
    "constant": (3, 6),
    "moderate": (5, 10),
    "slow": (3, 7),
}

_REPLY_LENGTH: dict[Pace, str] = {
    "constant": (
        "1-2 short sentences. Every extra line is a line they read instead of booking."
    ),
    "moderate": (
        "2-3 sentences, a little more warmth; one 'we've seen this before' line every "
        "few messages."
    ),
    "slow": (
        "3-4 sentences is fine — they read in isolated moments and need enough to feel "
        "it was worth opening."
    ),
}

_PACE_NOTE: dict[Pace, str] = {
    "constant": (
        "Lead is hot and on their phone. Do not over-serve. Aim to close by message 5-8."
    ),
    "moderate": (
        "Thoughtful lead, attention divided (maybe consulting a parent). Standard flow, "
        "same-day close."
    ),
    "slow": (
        "One message per day or big gaps. Pushing hard reads as desperate. Patient "
        "value, one reply per inbound, never two in a row."
    ),
}

_TONE_NOTE: dict[ToneStage, str] = {
    "curious_host": (
        "Curious host: warm, brief, ask before you tell. No pitching yet. Make them "
        "feel a human who cares about their situation."
    ),
    "helpful_expert": (
        "Helpful expert: small, specific, accurate nuggets that show you know this "
        "field. The first soft CTA lands in this zone."
    ),
    "bridge_builder": (
        "Bridge-builder: connect each concern they raise to something the counsellor "
        "handles. Make the direct ask for a call or an office visit."
    ),
    "honest_handoff": (
        "Honest handoff: \"I'm the assistant here and I don't want to keep giving you "
        "half-answers on something this important — the right person is our counsellor. "
        "Can I set up a quick call?\" Only works because it's true."
    ),
}

_CTA_NOTE: dict[CtaMode, str] = {
    "none": "No CTA yet — still building trust. End with a question that keeps them talking.",
    "soft": "Soft nudge: mention the call as the natural next step, lightly, once.",
    "direct": (
        "Direct CTA: ask plainly for a yes to a call OR an office visit (their choice). "
        "Never propose a clock time — the counsellor fixes the time. Free, ~15 min, "
        "no obligation. Offer to include a parent."
    ),
    "honest_handoff": (
        "Honest-handoff close: admit the bot's limits, name their specific concern once, "
        "say the counsellor is the right person, ask to set up the call or office visit."
    ),
    "nurture_soft": (
        "Past active pursuit. One friendly, low-pressure line or a single non-confidential "
        "fact, with a soft 'whenever you want to talk it through, the counsellor's here'. "
        "Do not chase."
    ),
}


@dataclass
class PacePlan:
    pace: Pace
    message_depth: int
    tone_stage: ToneStage
    cta_mode: CtaMode
    reply_length_hint: str
    pace_note: str
    tone_note: str
    cta_note: str


def classify_pace(minutes_since_last_bot: float | None) -> Pace:
    if minutes_since_last_bot is None:
        return "moderate"
    if minutes_since_last_bot < 5:
        return "constant"
    if minutes_since_last_bot < 120:
        return "moderate"
    return "slow"


def _tone_stage(depth: int, engagement_phase: str) -> ToneStage:
    if engagement_phase in ("handoff", "nurture"):
        return "honest_handoff"
    if depth <= 3:
        return "curious_host"
    if depth <= 8:
        return "helpful_expert"
    if depth <= 14:
        return "bridge_builder"
    return "honest_handoff"


def _cta_mode(pace: Pace, depth: int, engagement_phase: str) -> CtaMode:
    if engagement_phase == "nurture":
        return "nurture_soft"
    if engagement_phase == "handoff":
        return "honest_handoff"
    soft, direct = _CTA_THRESHOLDS[pace]
    if depth >= direct:
        return "direct"
    if depth >= soft:
        return "soft"
    return "none"


def plan_pace(
    *, message_depth: int, minutes_since_last_bot: float | None, engagement_phase: str
) -> PacePlan:
    pace = classify_pace(minutes_since_last_bot)
    tone = _tone_stage(message_depth, engagement_phase)
    cta = _cta_mode(pace, message_depth, engagement_phase)
    return PacePlan(
        pace=pace,
        message_depth=message_depth,
        tone_stage=tone,
        cta_mode=cta,
        reply_length_hint=_REPLY_LENGTH[pace],
        pace_note=_PACE_NOTE[pace],
        tone_note=_TONE_NOTE[tone],
        cta_note=_CTA_NOTE[cta],
    )
