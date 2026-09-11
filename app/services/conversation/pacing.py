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

Director review: the director's number should surface on genuine ENGAGEMENT —
real questions, follow-through, moving past surface-level chat — not a fixed
message count, while still reliably closing within roughly 7-8 exchanges. So
CTA timing is driven by two signals together: raw message depth (a ceiling that
guarantees it never drags on) and `substantive_depth` (how many of those
messages were actually substantive — computed in context.py from topic matches
+ extracted qualifiers), which can accelerate straight to a direct ask once the
lead is clearly engaged, without waiting for the ceiling. Depths 1-3 stay a
CTA-free floor either way — that window is for giving real value, not pitching
(see the "curious_host" tone note): the fix for "don't warm up too slowly" is
answering their actual question for real in that window, not asking sooner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Pace = Literal["constant", "moderate", "slow"]
ToneStage = Literal["curious_host", "helpful_expert", "bridge_builder", "honest_handoff"]
CtaMode = Literal["none", "soft", "direct", "honest_handoff", "nurture_soft"]

# soft-CTA / direct-CTA message-depth CEILINGS per pace (playbook Part 2) — the
# latest a genuinely quiet lead reaches each stage, guaranteeing a close by
# roughly message 7-8 even with no engagement signal to accelerate on. Soft
# never lands before depth 4 — depths 1-3 are the curious-host stage and carry
# no CTA at all (see `plan_pace`). `_ENGAGED_ACCEL` below lets a clearly
# engaged lead reach `direct` sooner than this ceiling. (moderate's old direct
# ceiling of 10 dragged well past the 7-8 target — tightened to 8.)
_CTA_THRESHOLDS: dict[Pace, tuple[int, int]] = {
    "constant": (4, 7),
    "moderate": (4, 8),
    "slow": (4, 8),
}
# Two genuinely substantive exchanges (real questions answered, or qualifiers
# given) is enough engagement to go straight to a direct ask rather than
# linger in "soft" waiting for the depth ceiling above.
_ENGAGED_ACCEL = 2

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
        "Lead is replying fast and on their phone. Keep replies short and match "
        "their length. Do not over-serve, and do not rush the booking — you have "
        "several turns; the CTA guidance says when."
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
        "Curious host: warm, brief, no PITCH — but if they ask something real, "
        "answer it for real. These first few messages are the critical window: "
        "give genuine, substantive value now, don't stall on rapport-building "
        "alone or they lose interest. No mention of a call, a meeting, the "
        "office, or the director yet — not even as 'the person who can help'. "
        "Confident, capable language on what you can do ('I can help you narrow "
        "down a country and get you into a university that fits') — not vague "
        "or hedgy."
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
        "Honest handoff: \"I don't want to keep giving you half-answers on "
        "something this important — the right person is our director. His number "
        "is there whenever you want to talk it through.\" Only works because it's "
        "true."
    ),
}

_CTA_NOTE: dict[CtaMode, str] = {
    "none": (
        "NO CTA this turn. Do not mention a call, a meeting, booking, the office, "
        "the director's number, or the director as 'who can really help'. End with "
        "a short question that keeps them talking, or just a warm reply. This "
        "overrides any general instinct to pitch."
    ),
    "soft": (
        "Soft nudge: mention a call with the director as the natural next step, "
        "lightly, once — as something they can do ('worth a quick call with him'), "
        "never as you arranging it or him calling them."
    ),
    "direct": (
        "Direct CTA: ask plainly for a yes to a call OR an office visit (their "
        "choice), framed as them reaching out to the director — give his number "
        "for them to call/message, never 'I'll set it up' or 'he'll call you'. No "
        "clock time. Free, ~15 min, no obligation."
    ),
    "honest_handoff": (
        "Honest-handoff close: be honest about your limits, name their specific "
        "concern once, say the director is the right person, and give his number "
        "for them to reach out — not 'shall I set it up'."
    ),
    "nurture_soft": (
        "Past active pursuit. One friendly, low-pressure line or a single "
        "non-confidential fact, with a soft 'whenever you want to talk it "
        "through, the director's number is there'. Do not chase."
    ),
}


@dataclass
class PacePlan:
    pace: Pace
    message_depth: int
    substantive_depth: int
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


def _cta_mode(pace: Pace, depth: int, engagement_phase: str, substantive_depth: int) -> CtaMode:
    if engagement_phase == "nurture":
        return "nurture_soft"
    if engagement_phase == "handoff":
        return "honest_handoff"
    soft, direct = _CTA_THRESHOLDS[pace]
    if depth >= direct:
        return "direct"
    if depth >= soft:
        # engagement-driven acceleration: once genuinely engaged, go straight
        # to direct rather than linger in soft waiting for the depth ceiling —
        # "surfaces on engagement signals, not a fixed message number".
        if substantive_depth >= _ENGAGED_ACCEL:
            return "direct"
        return "soft"
    if substantive_depth >= _ENGAGED_ACCEL:
        # earns at least a soft mention before the floor too — the curious-host
        # coherence check below still protects depths 1-3 either way.
        return "soft"
    return "none"


def plan_pace(
    *,
    message_depth: int,
    minutes_since_last_bot: float | None,
    engagement_phase: str,
    substantive_depth: int = 0,
) -> PacePlan:
    pace = classify_pace(minutes_since_last_bot)
    tone = _tone_stage(message_depth, engagement_phase)
    cta = _cta_mode(pace, message_depth, engagement_phase, substantive_depth)
    # Coherence: the curious-host stage never carries a CTA, whatever the depth
    # thresholds say. Tone and CTA must not give the model opposite instructions.
    if tone == "curious_host" and cta in ("soft", "direct"):
        cta = "none"
    return PacePlan(
        pace=pace,
        message_depth=message_depth,
        substantive_depth=substantive_depth,
        tone_stage=tone,
        cta_mode=cta,
        reply_length_hint=_REPLY_LENGTH[pace],
        pace_note=_PACE_NOTE[pace],
        tone_note=_TONE_NOTE[tone],
        cta_note=_CTA_NOTE[cta],
    )
