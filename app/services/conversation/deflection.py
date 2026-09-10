"""Deflection-mode framework (management file "Fallback & Deflection Variety").

The bot deflects a lot — legitimacy questions, "which university for me", premium
costs, stalls. Said once, "let me set up a call" sounds helpful; said three times
in one conversation it reads as a script and undoes the trust the rest of the
conversation is building.

The fix is 13 distinct deflection *modes*, each triggered by a different REASON
for deflecting, each with its own register. This module is a pure function of
inputs the engine already has: it picks the likely mode for a turn and resolves
the contact-detail + escalation rules. The LLM still does the writing — the modes
are register examples, never canned strings. The Response Guard fallback
(``app/services/guard/fallback.py``) uses the same modes for the last-resort
safe reply.

Rules encoded here:
  * Mode is chosen by why we're deflecting, not interchangeably.
  * Never number and address in the same message; default to the number; address
    only for modes 8 / 9 or when asked; the first deflect can carry no contact.
  * Escalate, don't repeat: first deflect soft / no contact, second carries the
    number, a third (or a repeat of the same mode) becomes mode 6 or 7.
  * Mode 12 (premium cost, PG cost, guarantees) is a permanent policy block —
    no data added later unlocks it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models.enums import RoleHint
from app.services.conversation.triage import Objection, TopicMatch

Contact = Literal["none", "number", "address", "address_brief"]


@dataclass(frozen=True)
class DeflectMode:
    num: int
    id: str
    title: str
    when: str
    register: str
    contact: Contact  # the channel this mode leans on when it does give contact
    permanent: bool = False


# --- the 13 modes -----------------------------------------------------------
# `register` is a voice example, not a string to send. Same posture, fresh words
# every time.
MODES: dict[int, DeflectMode] = {
    1: DeflectMode(
        1, "sensitive_topic", "Sensitive-topic deflect",
        "FMGE difficulty, licensing worries, 'will this degree work in India' — "
        "real fears with real answers that need a human's authority.",
        "\"I understand the worry about FMGE — it's more manageable than most "
        "people assume, especially with the right university choice. That "
        "university-matching part is where {name} really helps; worth talking it "
        "through with him directly.\"",
        "number",
    ),
    2: DeflectMode(
        2, "money_sensitive", "Money-sensitive deflect",
        "financing, refunds, payment schedules, consultancy fee amount.",
        "\"Money questions deserve a proper conversation, not a text — and I'd "
        "rather you get exact numbers from someone who can look at your specific "
        "case. You can call {name} on {phone} whenever suits you.\"",
        "number",
    ),
    3: DeflectMode(
        3, "personalized_assessment", "Personalized-assessment deflect",
        "'which university should I pick', 'what are my chances', 'is my score "
        "enough for X'.",
        "\"Genuinely can't call this properly without knowing your full profile — "
        "and guessing would do you a disservice. That's exactly what a 15-minute "
        "call sorts out.\"",
        "none",
    ),
    4: DeflectMode(
        4, "no_data_yet", "No-data-yet deflect",
        "a named university or country the bot has no confirmed detail on.",
        "\"That's a specific one — rather than give you a half-answer, {name} is "
        "the one who'd have the accurate detail. Worth checking with him.\"",
        "none",
    ),
    5: DeflectMode(
        5, "out_of_scope", "Out-of-scope deflect",
        "something outside Stellar's service entirely (other courses, unrelated "
        "countries).",
        "\"That's outside what we handle directly, so I don't want to guess. If "
        "it's connected to your MBBS plans, {name} will know where to point "
        "you.\"",
        "none",
    ),
    6: DeflectMode(
        6, "repeat_ask", "Repeat-ask deflect",
        "the lead asks the same thing again after a first deflect. Don't repeat "
        "the first answer — acknowledge the loop.",
        "\"I know I'm being unhelpful on this one and I'm sorry — it's genuinely "
        "not something I can answer accurately. {name} can, directly: {phone}.\"",
        "number",
    ),
    7: DeflectMode(
        7, "frustrated_lead", "Pushy/frustrated-lead deflect",
        "the lead is annoyed at not getting a straight answer.",
        "\"Fair — I'd want a straight answer too. On this I'd only be guessing, "
        "and you deserve better than a guess. {name} can give you the real "
        "answer on a call: {phone}.\"",
        "number",
    ),
    8: DeflectMode(
        8, "legitimacy_trust", "Legitimacy/trust deflect",
        "'how do I know you're real', 'are you a scam', 'prove it'.",
        "\"Completely fair to ask. We're at {address} — you're welcome to walk in "
        "and see the setup before deciding anything. That's usually more "
        "convincing than anything I can type.\"",
        "address",
    ),
    9: DeflectMode(
        9, "anxious_parent", "Emotional/anxious-parent deflect",
        "clear worry in the message — safety, homesickness, 'my child has never "
        "been away'.",
        "\"I hear you — this is your child, of course you're worried. Every parent "
        "we work with feels this, and it's the kind of thing that's better "
        "talked through with {name} directly than over messages.\"",
        "address",
    ),
    10: DeflectMode(
        10, "comparison_shopping", "Comparison-shopping deflect",
        "'another agent said X', 'why you and not them'.",
        "\"Fair thing to compare. Rather than talk down anyone else, I'd say check "
        "whether they only work with government institutes and whether the full "
        "cost is transparent upfront — those two questions separate most "
        "consultants. Happy for you to ask us the same on a call.\"",
        "none",
    ),
    11: DeflectMode(
        11, "stall_detection", "Stall-detection deflect",
        "'send me a brochure / full list / all details' — avoidance, not a real "
        "ask.",
        "\"I could send a PDF, but honestly it won't tell you what applies to "
        "*you* — and that's the part that matters. 15 minutes on a call does "
        "more.\"",
        "none",
    ),
    12: DeflectMode(
        12, "hard_blocked", "Hard-blocked-topic deflect",
        "premium-country figures, PG cost, admission guarantees — permanent "
        "no-go regardless of data.",
        "\"I'm not going to throw a number at you without context, because the "
        "honest answer really does depend on your case. {name} gives you the full "
        "picture properly.\"",
        "none",
        permanent=True,
    ),
    13: DeflectMode(
        13, "genuinely_unknown", "Genuinely-unknown deflect",
        "the bot simply has no idea and shouldn't pretend.",
        "\"Honestly, I don't know — and I'd rather say that than make something "
        "up. {name} will know.\"",
        "none",
    ),
}


@dataclass(frozen=True)
class DeflectionPlan:
    mode: DeflectMode
    contact: Contact
    deflect_index: int  # 0 = first deflect this conversation
    reason: str  # why this mode was picked (for the trace + the turn hint)
    escalated_from: int | None = None  # the mode we'd have picked before escalation


# --- trigger -> mode ------------------------------------------------------
_OBJECTION_MODE: dict[str, int] = {
    "legit": 8,
    "scam_fear": 8,
    "cheaper_elsewhere": 10,
    "send_everything": 11,
    "just_tell_me_here": 7,
}

# HARD_DEFLECT topics whose block is permanent policy, not missing data.
_PERMANENT_TOPICS = {"premium_cost", "pg_cost", "guarantee"}
_MONEY_TOPICS = {"financing", "scholarship", "payment_refund", "consultancy_fee"}
_ASSESSMENT_TOPICS = {"university_pick", "profile_assessment", "country_pick", "country_compare"}
_PARENT_TOPICS = {"parents_concern"}

# guard-block rules -> mode (the safe-fallback path)
_RULE_MODE: dict[str, int] = {
    "premium_cost_disclosure": 12,
    "pg_cost_mention": 12,
    "admission_guarantee": 12,
    "overpromise": 1,
    "cost_outside_approved_range": 2,
    "unapproved_cost_figure": 2,
    "blended_cost_range": 2,
    "sensitive_cost_needs_contact": 2,
    "cost_missing_inclusion": 2,
    "multi_country_cost": 3,
    "financing_mention": 2,
    "payment_terms_disclosure": 2,
    "meta_leak": 13,
    "reply_too_long": 3,
    "empty_reply": 13,
}


def mode_from_guard_rules(rules: list[str]) -> int:
    for r in rules:
        if r in _RULE_MODE:
            return _RULE_MODE[r]
    return 3


def _pick_mode(
    topic_match: TopicMatch | None,
    objection: Objection | None,
    speaker: RoleHint,
) -> tuple[int, str] | None:
    """The base mode for a live turn, before escalation. None => not a deflect."""

    if objection is not None and objection.id in _OBJECTION_MODE:
        return _OBJECTION_MODE[objection.id], f"objection:{objection.id}"

    if topic_match is not None:
        rule = topic_match.rule
        handling = topic_match.handling
        if rule.id in _PERMANENT_TOPICS:
            return 12, f"topic:{rule.id}"
        if rule.id in _MONEY_TOPICS:
            return 2, f"topic:{rule.id}"
        if rule.id in _PARENT_TOPICS or (rule.id == "safety" and speaker == RoleHint.PARENT):
            return 9, f"topic:{rule.id}"
        if rule.id == "special_case":
            return 4, f"topic:{rule.id}"
        if rule.id in _ASSESSMENT_TOPICS and handling in ("SOFT_DEFLECT", "PARTIAL"):
            return 3, f"topic:{rule.id}"
        if handling == "HARD_DEFLECT":
            return 12 if rule.id in _PERMANENT_TOPICS else 2, f"topic:{rule.id}"
        if handling == "SOFT_DEFLECT":
            return 3, f"topic:{rule.id}"

    # combos surface a hard-deflect secondary intent even when the dominant topic
    # is answerable — that part still needs a mode.
    if topic_match is not None and topic_match.hard_deflect_topics:
        sec = topic_match.hard_deflect_topics[0]
        if sec.id in _PERMANENT_TOPICS:
            return 12, f"secondary:{sec.id}"
        return 2, f"secondary:{sec.id}"

    return None


def select_deflection(
    *,
    topic_match: TopicMatch | None,
    objection: Objection | None,
    speaker: RoleHint = RoleHint.UNKNOWN,
    deflect_index: int = 0,
    repeat_detected: bool = False,
    last_mode: int | None = None,
    frustrated: bool = False,
    guard_blocked_rules: list[str] | None = None,
) -> DeflectionPlan | None:
    """Pick the deflection mode for this turn.

    ``guard_blocked_rules`` is the safe-fallback path — the LLM already failed to
    produce a compliant reply, so a mode is always returned. Otherwise a mode is
    returned only when the turn is actually a deflect (an objection, or SOFT/HARD
    handling, or a repeat), and ``None`` when the bot can just answer.
    """

    if guard_blocked_rules:
        base = mode_from_guard_rules(guard_blocked_rules)
        reason = f"guard:{','.join(guard_blocked_rules[:2])}"
    else:
        picked = _pick_mode(topic_match, objection, speaker)
        if picked is None and not repeat_detected:
            return None
        base = picked[0] if picked else 3
        reason = picked[1] if picked else "repeat"

    escalated_from: int | None = None
    mode_num = base

    # A permanent policy block never gets downgraded or escalated away.
    if base != 12:
        if repeat_detected or (last_mode is not None and last_mode == base):
            escalated_from, mode_num = base, 6
            reason = f"{reason}|repeat->6"
        elif deflect_index >= 2:
            escalated_from, mode_num = base, (7 if frustrated else 6)
            reason = f"{reason}|escalate->{mode_num}"
        elif frustrated and base not in (7, 8, 9):
            escalated_from, mode_num = base, 7
            reason = f"{reason}|frustrated->7"

    mode = MODES[mode_num]
    contact = _resolve_contact(mode, deflect_index, address_given=False)
    return DeflectionPlan(
        mode=mode,
        contact=contact,
        deflect_index=deflect_index,
        reason=reason,
        escalated_from=escalated_from,
    )


def _resolve_contact(mode: DeflectMode, deflect_index: int, *, address_given: bool) -> Contact:
    """Which contact detail (if any) this reply carries.

    Never number + address together. Modes 8/9 do their work with the address;
    everything else defaults to the number, and only from the second deflect on.
    The first deflect of a conversation carries no contact at all.
    """

    if mode.num in (8, 9):
        return "address_brief" if address_given else "address"
    if mode.permanent:
        # premium / PG / guarantee: hand off, but don't lead with a number until
        # the lead has been deflected once already.
        return "number" if deflect_index >= 1 else "none"
    if deflect_index <= 0:
        return "none"
    return "number"


# --- prompt rendering ----------------------------------------------------
def modes_reference(bot_name: str = "") -> str:
    """Compact always-on reference for the system prompt (not per-turn)."""

    who = bot_name.strip() or "the bot's name"
    lines = [
        "This applies ONLY when you are actually declining to answer something "
        "specific — a forbidden topic, a can't-know-without-your-case question, a "
        "stall. A greeting, small talk, or a question you can simply answer is "
        "NOT a deflection: just reply, no hand-off. Each turn, the per-turn "
        "guidance tells you whether a deflection mode is in play; if it doesn't, "
        "there is none.",
        "",
        "When you do deflect, it is not one line you reuse — there are 13 modes, "
        "picked by WHY you're deflecting. Same four beats: acknowledge -> reassure "
        "a little -> be honest about what you can and can't do -> point them to "
        "the director. Write it fresh each time; never send the same sentence "
        "twice in one conversation (if you would, you're in mode 6).",
        "",
        f"In every register: you are still {who} (not 'the assistant'), and the "
        "direction of contact is the lead reaching out to the director — never "
        "'he'll call you' or 'I'll set it up'.",
        "",
    ]
    contact_label = {
        "none": "no contact",
        "number": "number",
        "address": "address",
        "address_brief": "address",
    }
    for m in MODES.values():
        lines.append(f"{m.num}. {m.title} — {m.when} [{contact_label[m.contact]}]")
    lines += [
        "",
        "Contact rules: never the number and the address in the same message. "
        "Default to the number (the director's direct line). Give the address "
        "only for modes 8 and 9, or when the lead asks about visiting / is local "
        "/ raises a legitimacy concern — and give it once, briefly, if asked "
        "again. The first deflect in a conversation can carry no contact at all; "
        "the second carries the number; a third becomes mode 6 or 7.",
        "Mode 12 is permanent — premium-country figures, PG cost and admission "
        "guarantees stay blocked by policy no matter what data exists.",
    ]
    return "\n".join(lines)


def turn_hint(
    plan: DeflectionPlan,
    *,
    counselor_name: str = "",
    counselor_phone: str = "",
    office_address: str = "",
) -> str:
    """The per-turn block: the selected mode's full register + resolved contact."""

    m = plan.mode
    register = m.register.replace("{name}", counselor_name or "the director")
    register = register.replace("{phone}", counselor_phone or "the director's number")
    register = register.replace("{address}", office_address or "our office")
    him = counselor_name or "the director"

    contact_directive = {
        "none": "Do NOT include a phone number or the address this turn — pointing "
        f"them to {him} is enough, no number.",
        "number": f"Give {him}'s number ({counselor_phone or 'the direct line'}) "
        "for THEM to call or message him — never as him contacting them. Not the "
        "address.",
        "address": f"Give the office address ({office_address or 'the office'}) in this "
        "reply — this is the mode where the address does the work. Not the number.",
        "address_brief": "The address has already been shared — if it's asked for "
        "again, give it plainly in one line, no re-pitch. Not the number.",
    }[plan.contact]

    lines = [
        "\n## Deflection register (management file: 13-mode system)\n",
        f"mode: {m.num} — {m.title}\n",
        f"why: {plan.reason}\n",
        f"this is deflect #{plan.deflect_index + 1} in the conversation\n",
    ]
    if plan.escalated_from is not None:
        lines.append(
            f"escalated: you'd normally use mode {plan.escalated_from} here, but "
            "you've already deflected this — don't run the same move again\n"
        )
    lines += [
        f"register (voice example — write fresh, do not copy): {register}\n",
        f"contact: {contact_directive}\n",
    ]
    return "".join(lines)
