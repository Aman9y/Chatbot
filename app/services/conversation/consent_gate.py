"""Conversational opt-in + age gate (build-plan §2 / DPDP).

Runs before any sales/qualification turn. Two questions, in order:

  1. opt-in ask  -> yes / no / unclear
  2. age check   -> adult / minor / unclear

Replies are read by the LLM/NLU layer (heuristics are a fast path for the
obvious cases and carry the fake provider in tests). An unclear answer is
re-asked once, phrased differently; still unclear -> parked for a human. A
"no" -> respectful close + opt-out. A confirmed minor -> stop, hold pending
guidance, flag the counsellor. Never treat an unclear answer as a yes.

The under-18 handling here is a safe placeholder — what actually happens with a
confirmed-minor lead (parent outreach, retention) is an open legal question, not
finalised in code.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from app.config import Settings
from app.services.llm.base import LLMClient, LLMMessage

OptInVerdict = Literal["yes", "no", "unclear"]
AgeVerdict = Literal["adult", "minor", "unclear"]


# --- canned copy ------------------------------------------------------
def _company(settings: Settings) -> str:
    return settings.company_name.strip() or "our team"


def consent_ask(settings: Settings) -> str:
    return (
        f"Hi, this is {_company(settings)} — we help students who've appeared for "
        "NEET explore MBBS abroad options. Would you like to hear more?"
    )


def consent_reask(settings: Settings) -> str:
    return (
        "Just to check I've got this right — would you like me to share how the "
        "MBBS-abroad options work? A simple yes or no is completely fine."
    )


def age_ask(settings: Settings) -> str:
    return (
        "Great — one quick check before we continue: are you 18 or older, or "
        "under 18?"
    )


def age_reask(settings: Settings) -> str:
    return (
        "Sorry, I need a clear answer on this one before we go further — are you "
        "18 or above, or below 18?"
    )


def decline_close(settings: Settings) -> str:
    return (
        "No problem at all — we're here anytime if that changes. All the best "
        "with your NEET journey."
    )


def minor_hold_message(settings: Settings) -> str:
    return (
        "Thanks for letting me know. For students under 18 we bring a parent or "
        "guardian into the conversation before continuing — someone from our team "
        "will be in touch."
    )


def review_hold_message(settings: Settings) -> str:
    return (
        "Thanks — I'll have someone from our team follow up with you directly on "
        "this."
    )


# --- heuristic interpretation ----------------------------------------
# strong "no" phrases override any incidental "yes" word (e.g. "not interested"
# contains "interested")
_STRONG_NO = re.compile(
    r"\b(not interested|no thanks?|no thank you|don'?t want|dont want|do not want|"
    r"not now|not really|leave it|rehne do|mujhe nahi|nahi chahiye|nahi interested|"
    r"not looking|remove me|unsubscribe|stop contacting|please stop)\b",
    re.IGNORECASE,
)
_YES = re.compile(
    r"\b(yes|yes please|yeah|yep|yup|ya|sure|ok|okay|okie|"
    r"haan|haa|ha ji|haanji|haan ji|jee|ji haan|bilkul|zaroor|"
    r"go ahead|tell me more|tell me|batao|bataiye|bata do|"
    r"interested|i'?m interested|i am interested|keen|"
    r"sounds good|why not|of course|definitely|absolutely|please do|"
    r"kyun nahi|kyu nahi|chahiye|chahiye info)\b",
    re.IGNORECASE,
)
_NO = re.compile(
    r"\b(no|nope|nah|naa|nahi|nahin)\b",
    re.IGNORECASE,
)

_AGE_NUM = re.compile(r"(?<![\d/.])(\d{1,2})(?![\d/])")
_AGE_CONTEXT = re.compile(
    r"\b(year|years|yr|yrs|old|age|aged|saal|sal|turning|turned|completed|"
    r"i am|i'?m|im|main|abhi|dob|born)\b",
    re.IGNORECASE,
)
_ADULT_PHRASE = re.compile(
    r"(18\s*\+|18\s*plus|18 or (?:older|above|more|up)|over 18|above 18|"
    r"older than 18|more than 18|\badult\b|\bmajor\b|\bbalig\b|18 se (?:upar|zyada|jyada)|"
    r"yes,? (?:i'?m |i am )?(?:over |above )?18|i'?m an adult)",
    re.IGNORECASE,
)
_MINOR_PHRASE = re.compile(
    r"(under 18|below 18|less than 18|not 18(?: yet)?|under-?age|\bminor\b|"
    r"\bnabalig\b|18 se (?:kam|neeche|niche)|abhi 18 nahi|haven'?t turned 18|"
    r"i'?m (?:only )?1[0-7]\b|17 (?:year|saal)|going to be 18)",
    re.IGNORECASE,
)


def heuristic_optin(text: str) -> OptInVerdict:
    if not text or not text.strip():
        return "unclear"
    if _STRONG_NO.search(text):
        return "no"
    yes = bool(_YES.search(text))
    no = bool(_NO.search(text))
    if yes and not no:
        return "yes"
    if no and not yes:
        return "no"
    return "unclear"


def heuristic_age(text: str) -> AgeVerdict:
    if not text or not text.strip():
        return "unclear"
    if _MINOR_PHRASE.search(text):
        return "minor"
    if _ADULT_PHRASE.search(text):
        return "adult"
    # a bare stated age
    for m in _AGE_NUM.finditer(text):
        val = int(m.group(1))
        if not (5 <= val <= 99):
            continue
        window = text[max(0, m.start() - 20) : m.end() + 20]
        stripped = re.sub(r"[^0-9a-z ]", "", text.lower()).strip()
        if _AGE_CONTEXT.search(window) or stripped == str(val) or len(stripped) <= 4:
            return "minor" if val < 18 else "adult"
    return "unclear"


# --- LLM refinement --------------------------------------------------
_OPTIN_SYSTEM = (
    "A person got this WhatsApp message from an MBBS-abroad consultancy: "
    '"Would you like to hear more?" Read their reply and decide if it is a clear '
    "YES, a clear NO, or UNCLEAR. Understand English, Hindi and mixed "
    '(haan/nahi/bilkul/nahi chahiye etc.). If it is a question back, a greeting, '
    'or anything not a clear yes/no, answer "unclear". '
    'Reply as JSON: {"answer": "yes|no|unclear"}.'
)
_AGE_SYSTEM = (
    "A person was asked on WhatsApp: \"Are you 18 or older, or under 18?\" Read "
    "their reply and decide: ADULT (clearly 18+), MINOR (clearly under 18), or "
    "UNCLEAR. A stated age counts (\"I'm 20\" -> adult, \"17\" -> minor). A bare "
    '"yes"/"no" is UNCLEAR because the question has two options. Understand Hindi '
    "and mixed. "
    'Reply as JSON: {"answer": "adult|minor|unclear"}.'
)


async def _classify(
    text: str,
    *,
    system: str,
    allowed: set[str],
    llm: LLMClient | None,
    model: str | None,
    use_llm: bool,
) -> str | None:
    if not (use_llm and llm is not None and model and text.strip()):
        return None
    try:
        resp = await llm.complete(
            system=system,
            messages=[LLMMessage(role="user", content=text[:600])],
            model=model,
            max_output_tokens=40,
            json_mode=True,
            purpose="consent_gate",
        )
        data = json.loads(resp.text or "{}")
        answer = str(data.get("answer", "")).lower().strip()
        return answer if answer in allowed else None
    except Exception:  # noqa: BLE001 - gate interpretation never raises
        return None


async def interpret_optin_reply(
    text: str,
    *,
    llm: LLMClient | None = None,
    model: str | None = None,
    use_llm: bool = False,
) -> OptInVerdict:
    heuristic = heuristic_optin(text)
    if heuristic != "unclear":
        return heuristic
    llm_answer = await _classify(
        text,
        system=_OPTIN_SYSTEM,
        allowed={"yes", "no", "unclear"},
        llm=llm,
        model=model,
        use_llm=use_llm,
    )
    return llm_answer or "unclear"  # never default to yes


async def interpret_age_reply(
    text: str,
    *,
    llm: LLMClient | None = None,
    model: str | None = None,
    use_llm: bool = False,
) -> AgeVerdict:
    heuristic = heuristic_age(text)
    if heuristic != "unclear":
        return heuristic
    llm_answer = await _classify(
        text,
        system=_AGE_SYSTEM,
        allowed={"adult", "minor", "unclear"},
        llm=llm,
        model=model,
        use_llm=use_llm,
    )
    return llm_answer or "unclear"  # never default to adult


def stated_age(text: str) -> int | None:
    """Best-effort explicit age for the lead row (only when clearly an age)."""

    for m in _AGE_NUM.finditer(text or ""):
        val = int(m.group(1))
        if 5 <= val <= 99:
            window = text[max(0, m.start() - 20) : m.end() + 20]
            if _AGE_CONTEXT.search(window) or re.sub(r"[^0-9]", "", text) == str(val):
                return val
    return None
