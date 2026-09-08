"""Deterministic Tier-1 content detectors for the Response Guard.

These implement the hard rules in plan §2 that CAN be enforced in code (critique
B1). Each returns a list of (rule, matched_text, detail) tuples. They are
deliberately biased toward false positives — a blocked-then-regenerated reply is
cheap, a leaked premium cost or admission guarantee is not.
"""

from __future__ import annotations

import re

Match = tuple[str, str, str]  # (rule, matched_text, detail)

# --- money / cost figures ------------------------------------------------

_CURRENCY = r"(?:₹|rs\.?|inr|usd|us\$|\$|eur|€|gbp|£|aed|cad)"
_SCALE = r"(?:lakhs?|lacs?|lakh|crores?|cr|k\b|thousand|million|mn|billion)"

_MONEY_PATTERNS = [
    # currency symbol/code followed by a number (optionally a scale word)
    re.compile(rf"{_CURRENCY}\s?\d[\d,.\s]*\s*{_SCALE}?", re.IGNORECASE),
    # number followed by a scale word:  40 lakh / 1.5 crore / 40k / 50 thousand
    re.compile(
        r"(?<![\w.])\d[\d,]*(?:\.\d+)?\s*(?:lakhs?|lacs?|crores?|cr|k|thousand|million)\b",
        re.IGNORECASE,
    ),
    # "40L" / "35 L" — capital-L lakh shorthand (case-sensitive to avoid false hits)
    re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?\s?L\b"),
    # number-word followed by a scale word:  forty lakh / thirty-five lakhs / one crore
    re.compile(
        r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
        r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|"
        r"[a-z]+[- ](?:one|two|three|four|five|six|seven|eight|nine))"
        r"[- ]?(?:lakhs?|lacs?|crores?|thousand|million)\b",
        re.IGNORECASE,
    ),
]

_COST_CONTEXT = re.compile(
    r"\b(cost|costs|fee|fees|tuition|package|total|budget|expense|expenses|"
    r"price|charges?|amount|investment|spend|paisa|rupees?)\b",
    re.IGNORECASE,
)
_BARE_LARGE_NUMBER = re.compile(r"(?<![\w.])\d{5,}(?![\d])")

_APPROX = re.compile(
    r"\b(around|about|roughly|approx\.?|approximately|nearly|starting (?:from|at)|"
    r"upto|up to|in the range of|ballpark|somewhere around)\b",
    re.IGNORECASE,
)


def find_money(text: str) -> list[str]:
    hits: list[str] = []
    for pattern in _MONEY_PATTERNS:
        hits += [m.group(0).strip() for m in pattern.finditer(text)]
    # bare 5+ digit numbers, but only when a cost word is in the sentence
    for m in _BARE_LARGE_NUMBER.finditer(text):
        window = text[max(0, m.start() - 60) : m.end() + 60]
        if _COST_CONTEXT.search(window):
            hits.append(m.group(0))
    # de-dup, drop obvious non-money ("2024", years already excluded by <5 digits)
    seen: list[str] = []
    for h in hits:
        norm = h.lower().strip()
        if norm and norm not in seen:
            seen.append(norm)
    return seen


# --- India-comparison context ----------------------------------------

_INDIA_CONTEXT = re.compile(
    r"\b(india|indian|domestic|back home|private (?:medical )?colleges?|"
    r"private (?:medical )?colleges?|deemed (?:university|college)|mgmt quota|"
    r"management quota|neet[- ]?ug seat|government seat)\b",
    re.IGNORECASE,
)


def india_context(text: str) -> bool:
    """True when the text is talking about MBBS *in India* — the only context in
    which the India-private cost range may be stated (plan §2 comparison use)."""

    return bool(_INDIA_CONTEXT.search(text))


# --- premium countries -------------------------------------------------

def premium_country_mentioned(text: str, premium_countries: list[str]) -> str | None:
    lowered = text.lower()
    for country in premium_countries:
        c = country.lower().strip()
        if not c:
            continue
        if re.search(rf"\b{re.escape(c)}\b", lowered):
            return country
    return None


# --- financing / loans -------------------------------------------------

_FINANCING = re.compile(
    r"\b(loan|loans|e\.?m\.?i\.?|instal?lments?|financing|"
    r"education loan|student loan|no[- ]cost emi|pay later|pay in parts|"
    r"pay in instal?lments|mortgage|collateral|मुद्रा ऋण|लोन|कर्ज|किश्त|"
    r"kist|kisht|karza?|udhaar?|byaj)\b",
    re.IGNORECASE,
)


def find_financing(text: str) -> list[str]:
    return [m.group(0) for m in _FINANCING.finditer(text)]


# --- payment schedules / refund + cancellation terms (plan §2 rule 7) --

_PAYMENT_TERM = re.compile(
    r"\b(refunds?|refundable|refunded|refunding|non[- ]?refundable|"
    r"cancellation (?:policy|fee|fees|charges?|terms?|penalty|penalties)|"
    r"cancell?ation (?:is|will)|"
    r"payment (?:schedule|plan|terms?|milestones?|structure|breakup|break[- ]?up)|"
    r"part[- ]payment|part[- ]payments|money[- ]back|"
    r"forfeit|forfeited|forfeits|forfeiture|"
    r"(?:deposit|advance|token|registration fee) is (?:fully |partially |non[- ]?)?"
    r"(?:refundable|non[- ]?refundable|adjustable|adjusted)|"
    r"पैसा वापस|रिफ़?ंड|वापसी|पैसे वापस)\b",
    re.IGNORECASE,
)
# When the reply is DEFLECTING the topic to the counsellor / to writing, the
# term word appears but no actual term is being stated — that is allowed.
_PAYMENT_DEFLECTION = re.compile(
    r"\b(counsel?lor|counsel?ling team|on (?:the|a|our) call|in person|in writing|"
    r"over (?:the )?call|covers? (?:this|that|it|the)|explains?|walk (?:you )?through|"
    r"goes? through|discuss(?:es|ed)?|not something (?:i|we) (?:do|handle|cover)|"
    r"needs? to be in writing|proper(?:ly)? explained|shared? properly|"
    r"the counsel?lor (?:covers|goes|explains|will))\b",
    re.IGNORECASE,
)


def find_payment_terms(text: str) -> list[str]:
    hits: list[str] = []
    for m in _PAYMENT_TERM.finditer(text):
        window = text[max(0, m.start() - 100) : m.end() + 100]
        if _PAYMENT_DEFLECTION.search(window):
            continue
        hits.append(m.group(0))
    seen: list[str] = []
    for h in hits:
        if h.lower() not in seen:
            seen.append(h.lower())
    return seen


# --- admission / outcome guarantees ----------------------------------

_GUARANTEE_TERM = re.compile(
    r"\b(guarantee[ds]?|guaranteeing|assured|assure you|100\s?%|cent per cent|"
    r"sure[- ]shot|definitely (?:get|be|have)|will definitely|no doubt|"
    r"promise you|rest assured|for sure|certainly get|confirmed|reserved for you)\b",
    re.IGNORECASE,
)
_OUTCOME_WORD = re.compile(
    r"\b(admission|admissions|seat|seats|mbbs|intake|selected|selection|"
    r"confirmed|get in|getting in|university|college|placement)\b",
    re.IGNORECASE,
)
_CONFIRMED_SEAT = re.compile(
    r"\b(confirmed|guaranteed|assured|reserved)\s+(seat|admission|mbbs|intake)\b",
    re.IGNORECASE,
)


def find_guarantees(text: str) -> list[str]:
    hits: list[str] = []
    for m in _CONFIRMED_SEAT.finditer(text):
        hits.append(m.group(0))
    for m in _GUARANTEE_TERM.finditer(text):
        window = text[max(0, m.start() - 50) : m.end() + 50]
        if _OUTCOME_WORD.search(window):
            hits.append(m.group(0))
    seen: list[str] = []
    for h in hits:
        if h.lower() not in seen:
            seen.append(h.lower())
    return seen


# --- PG (postgraduate) cost ----------------------------------------

_PG_CONTEXT = re.compile(
    r"\b(pg|post[- ]?graduate|post[- ]?grad|postgrad|next exam|\bnext\b|"
    r"pg medical|md/ms|md ms|residency|specialisation|specialization)\b",
    re.IGNORECASE,
)


def find_pg_cost(text: str) -> list[str]:
    money = find_money(text)
    if not money:
        return []
    for m in _PG_CONTEXT.finditer(text):
        window = text[max(0, m.start() - 80) : m.end() + 80]
        if find_money(window):
            return [f"PG context near figure: {m.group(0)}"]
    return []


# --- meta / prompt leakage -----------------------------------------

_META_LEAK = re.compile(
    r"(system prompt|\bmy instructions\b|\bmy system prompt\b|as an ai language model|"
    r"\bi am an ai\b|i'm an ai(?! assistant)|"
    r"ignore (?:the |your )?(?:previous |above )?instructions|"
    r"\{\{[a-z_]+\}\}|</?[a-z_]{3,}>)",
    re.IGNORECASE,
)


def find_meta_leak(text: str) -> list[str]:
    return [m.group(0) for m in _META_LEAK.finditer(text)]


def is_approx_cost_language(text: str) -> bool:
    return bool(_APPROX.search(text) and _COST_CONTEXT.search(text))
