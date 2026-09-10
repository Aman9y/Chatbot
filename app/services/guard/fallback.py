"""Safe canned replies used when the guard blocks a draft it cannot fix.

Not one fixed string — a small pool per deflection mode (see
``app/services/conversation/deflection.py``). The engine passes the mode it
resolved from the blocked rules plus the recent bot text, and we pick a phrasing
that has not already been used in this conversation. Repetition is what makes a
deflect read as a script; variety is the whole point.
"""

from __future__ import annotations

import re

from app.config import Settings
from app.services.conversation.deflection import DeflectionPlan, select_deflection

# Phrasing pools keyed by deflection mode number. Each is a complete, compliant
# reply. `{name}` -> counsellor name, `{phone}` -> counsellor phone,
# `{address}` -> office address. Lines that reference a channel are only offered
# when the plan's resolved contact allows it (see `_contact_ok`).
_POOLS: dict[int, list[str]] = {
    1: [
        "That worry is a fair one, and honestly it's more manageable than most "
        "people assume with the right university. {name} has done this for years "
        "and can walk you through it properly — worth a quick call?",
        "This is exactly the kind of thing {name} is better placed to answer than "
        "I am. Can I set up a short call so you get it first-hand?",
    ],
    2: [
        "Money questions deserve a proper conversation, not a text — {name} can "
        "look at your specific case and give you exact numbers. Reach him on "
        "{phone} whenever suits you.",
        "I'd rather you got the real figures from {name} directly than a vague "
        "answer from me. A quick call sorts it out — shall I set one up?",
    ],
    3: [
        "I genuinely can't call this well without your full picture, and guessing "
        "would do you a disservice. That's exactly what a 15-minute call with "
        "{name} is for — shall I set it up?",
        "This one really depends on your specific situation. {name} can give you a "
        "proper read on a short call — want me to arrange it?",
    ],
    4: [
        "That's a specific one — rather than give you a half-answer, let me get "
        "you the accurate version from {name}.",
        "I don't want to guess on that. {name} will have the accurate detail — "
        "can I set up a quick call?",
    ],
    5: [
        "That's outside what we handle directly, so I don't want to guess. If "
        "it's tied to your MBBS plans, {name} can point you the right way.",
    ],
    6: [
        "I know I keep coming up short on this and I'm sorry — it's genuinely not "
        "something I can answer accurately. {name} can, directly: {phone}.",
        "I'm clearly not the right one to answer this properly, and I don't want "
        "to keep you going in circles. {name} can help directly on {phone}.",
    ],
    7: [
        "Fair — I'd be frustrated too. I'm a basic assistant, so here I'd just be "
        "guessing, and you deserve better than that. One call with {name} and "
        "you'll have the real answer.",
        "You're right to want a straight answer. I can't give you an accurate one "
        "on this — but {name} can, on a short call.",
    ],
    8: [
        "Completely fair to ask. We're at {address} — you're welcome to walk in "
        "and see the setup before deciding anything.",
        "That's a fair question. Come see us at {address} — meeting the team in "
        "person tends to answer it better than anything I can type.",
    ],
    9: [
        "I hear you — this is your child, of course you're worried. Every parent "
        "we work with feels this, and it's exactly why {name} prefers speaking "
        "with parents directly. Can I set that up?",
        "That concern is completely understandable. {name} talks parents through "
        "this properly — would you like me to arrange a call?",
    ],
    10: [
        "Fair thing to compare. I'd just check whether they work only with "
        "government institutes and whether the full cost is transparent upfront — "
        "those two questions separate most consultants. Happy for you to ask us "
        "the same on a call.",
    ],
    11: [
        "I could send a document, but honestly it won't tell you what applies to "
        "you — and that's the part that matters. Fifteen minutes with {name} does "
        "more. Shall I set it up?",
        "A generic pack won't answer what's specific to your case. A short call "
        "with {name} will — want me to arrange one?",
    ],
    12: [
        "I'm not going to put a number on that without context — the honest "
        "answer really does depend on your case. {name} gives you the full "
        "picture properly on a call.",
        "That's not something I can give you a figure for — it varies too much by "
        "situation. {name} covers it accurately on a short call.",
    ],
    13: [
        "Honestly, I don't know that one — and I'd rather say so than make "
        "something up. {name} will know.",
        "I don't want to guess at that. {name} can give you a proper answer — "
        "shall I set up a call?",
    ],
}

# nurture phase: never chase, no number push
_NURTURE = [
    "Whenever you'd like to talk it through, {name} is here — just say the word "
    "and I'll set up a short call.",
    "No rush at all. When you want a proper look at your options, {name} is happy "
    "to help — just message here.",
]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", (text or "").lower())).strip()


def _contact_ok(line: str, plan: DeflectionPlan) -> bool:
    wants_phone = "{phone}" in line
    wants_address = "{address}" in line
    if wants_phone and plan.contact not in ("number",):
        return False
    if wants_address and plan.contact not in ("address", "address_brief"):
        return False
    return True


def _fill(line: str, settings: Settings) -> str:
    name = settings.counselor_name.strip() or "our counsellor"
    return (
        line.replace("{name}", name)
        .replace("{phone}", settings.counselor_phone.strip() or "his direct line")
        .replace("{address}", settings.office_address.strip() or "our office")
    )


def safe_fallback_message(
    settings: Settings,
    *,
    engagement_phase: str = "push",
    plan: DeflectionPlan | None = None,
    blocked_rules: list[str] | None = None,
    prior_bot_text: str = "",
) -> str:
    """Pick a compliant fallback in the right deflection register.

    ``plan`` is normally resolved by the engine; if absent it is derived from
    ``blocked_rules``. ``prior_bot_text`` is the recent bot side of the
    conversation — any phrasing already used there is skipped so we don't repeat.
    """

    if plan is None:
        plan = select_deflection(
            topic_match=None,
            objection=None,
            guard_blocked_rules=blocked_rules or ["cost_missing_inclusion"],
        )

    if engagement_phase == "nurture":
        pool = _NURTURE
    else:
        pool = _POOLS.get(plan.mode.num if plan else 3, _POOLS[3])

    seen = _norm(prior_bot_text)
    candidates = [ln for ln in pool if plan is None or _contact_ok(ln, plan)]
    if not candidates:
        candidates = [
            re.sub(r"\s*(?:on |at )?\{(?:phone|address)\}", "", ln) for ln in pool
        ]

    for line in candidates:
        filled = _fill(line, settings)
        if _norm(filled)[:60] not in seen:
            return filled

    # everything in the pool has been used — fall back to a plain, always-safe line
    name = settings.counselor_name.strip() or "our counsellor"
    return (
        f"This is one for {name} rather than me — can I set up a short call so you "
        "get an accurate answer?"
    )
