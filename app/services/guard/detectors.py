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

# Country tokens that collide with ordinary English words, so they only count as
# a country when the case matches: "US" the country vs "us" the pronoun. Without
# this, "let us set up a call" reads as a premium country being in scope and
# every approved figure alongside it is blocked as a premium leak.
_CASE_SENSITIVE_COUNTRY_CODES = {"us"}


def premium_country_mentioned(text: str, premium_countries: list[str]) -> str | None:
    for country in premium_countries:
        c = country.strip()
        if not c:
            continue
        flags = 0 if c.lower() in _CASE_SENSITIVE_COUNTRY_CODES else re.IGNORECASE
        if re.search(rf"\b{re.escape(c)}\b", text, flags):
            return country
    return None


# Countries this product actually discusses. Used to answer "is a country in
# scope that we are not allowed to attach a figure to?" — i.e. anything named
# here that is neither in COUNTRY_COST_RANGES (approved to quote) nor in
# PREMIUM_COST_COUNTRIES (never quote — handled separately, louder). Kept
# data-driven so it stays correct when the config lists change.
_KNOWN_COUNTRIES: tuple[str, ...] = (
    "kazakhstan", "uzbekistan", "kyrgyzstan", "georgia", "russia", "armenia",
    "belarus", "ukraine", "moldova", "poland", "latvia", "lithuania", "bulgaria",
    "romania", "serbia", "bosnia", "turkey", "china", "bangladesh", "nepal",
    "philippines", "egypt", "iran", "mauritius", "guyana",
    "germany", "united kingdom", "uk", "united states", "usa", "canada",
    "australia", "ireland", "new zealand",
)


def unpriceable_country_mentioned(
    text: str,
    *,
    approved_countries: list[str],
    premium_countries: list[str],
) -> str | None:
    """First known country in `text` that no approved cost range covers.

    A figure must not ride alongside such a country even when the figure itself
    is an approved-tier one — the lead would read it as that country's price.
    Premium countries are exempt here only because they raise their own, louder
    violation upstream (``premium_cost_disclosure``).
    """

    lowered = text.lower()
    exempt = {
        c.lower().strip()
        for c in (*approved_countries, *premium_countries)
        if c.strip()
    }
    for country in _KNOWN_COUNTRIES:
        if country in exempt:
            continue
        if re.search(rf"\b{re.escape(country)}\b", lowered):
            return country.title()
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


# A guarantee word is a VIOLATION only when it's an assertion. An honest denial —
# "admission is never guaranteed", "no consultancy can guarantee a seat",
# "hum guarantee nahi de sakte" — must pass through, not get blocked and swapped
# for the fallback. Negators are checked just before the match (English: "no /
# not / never / cannot / can't / without / nobody can …") and just after it
# (Hindi is verb-final: "guarantee nahi …").
_GUARANTEE_NEG_BEFORE = re.compile(
    r"\b(no|not|never|without|cannot|can'?t|cant|won'?t|wont|would ?n'?t|do ?n'?t"
    r"|dont|does ?n'?t|is ?n'?t|are ?n'?t|was ?n'?t|nobody|no[- ]?one|none|nothing"
    r"|zero|neither|nor|hardly)\b|n'?t\b",
    re.IGNORECASE,
)
_GUARANTEE_NEG_AFTER = re.compile(r"^\W{0,4}(nahi+n?|not\b)", re.IGNORECASE)


def _guarantee_negated(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 32):start]
    after = text[end:end + 22]
    return bool(_GUARANTEE_NEG_BEFORE.search(before) or _GUARANTEE_NEG_AFTER.search(after))


def find_guarantees(text: str) -> list[str]:
    hits: list[str] = []
    for m in _CONFIRMED_SEAT.finditer(text):
        if not _guarantee_negated(text, m.start(), m.end()):
            hits.append(m.group(0))
    for m in _GUARANTEE_TERM.finditer(text):
        window = text[max(0, m.start() - 50) : m.end() + 50]
        if _OUTCOME_WORD.search(window) and not _guarantee_negated(text, m.start(), m.end()):
            hits.append(m.group(0))
    seen: list[str] = []
    for h in hits:
        if h.lower() not in seen:
            seen.append(h.lower())
    return seen


# --- overpromising / overselling (review notes §3) -------------------
# The mirror of the leak detectors: these catch the bot drifting toward comfort —
# implying admission is easy, FMGE is trivial, outcomes are assured, risk is
# nil, safety is absolute. The audience is 17-year-olds and anxious parents
# making an irreversible ₹30L+ decision; an overpromise that leads a family to
# commit is a worse outcome than a deflection that loses a lead. Honest
# reassurance is fine ("more manageable than most people assume", "safer than
# students expect", "many students clear the FMGE") — these patterns are tuned
# to miss those and catch the assertions of certainty / ease.

_OVERPROMISE = re.compile(
    r"("
    # admission / getting in is certain or easy
    r"admissions?\s+(?:is|are|will be)\s+(?:basically\s+|pretty much\s+|practically\s+"
    r"|just\s+|more or less\s+|almost\s+)?(?:a\s+)?(?:formality|guaranteed|sorted|"
    r"as good as done|done deal|certain|in the bag)"
    r"|(?:getting|to get)\s+(?:in|admission|a seat)\s+(?:\w+\s+){0,2}is\s+"
    r"(?:easy|simple|guaranteed|no (?:problem|trouble|issue)|a formality|"
    r"straightforward|a breeze)"
    r"|easy\s+to\s+get\s+(?:in|admission|a seat)"
    r"|you(?:'ll| will| are|'re)\s+(?:definitely\s+|certainly\s+|surely\s+|easily\s+"
    r"|100%\s+|for sure\s+|sure to\s+|guaranteed to\s+)?(?:get\s+"
    r"(?:in\b|admission|a seat|selected)"
    r"(?!\s+(?:support|help|guidance|assistance|counsel|advice|process|touch|docs?|documents?))"
    r"|be\s+(?:selected|admitted))"
    r"|your\s+(?:admission|seat)\s+is\s+(?:confirmed|sorted|secure|guaranteed"
    r"|as good as done|basically done|in the bag)"
    r"|no\s+(?:trouble|problem|issue|difficulty|worries?)\s+(?:getting|with)\s+"
    r"(?:in|admission|a seat)"
    # FMGE / licensing downplayed
    r"|(?:fmge|next exam|the screening (?:exam|test)|licen[cs]ing exam)\s+"
    r"(?:is|is going to be|will be)\s+(?:pretty\s+|quite\s+|very\s+|really\s+|so\s+"
    r"|that\s+|totally\s+|super\s+|basically\s+|just\s+|pretty much\s+|practically\s+"
    r"|more or less\s+|almost\s+)?(?:easy|simple|straightforward|a formality"
    r"|no big deal|nothing (?:to worry about|serious|much))"
    r"|(?:fmge|next exam|the screening (?:exam|test)|licen[cs]ing exam)\s+"
    r"(?:isn'?t|is not|won'?t be|ain'?t)\s+(?:that\s+|so\s+|really\s+|very\s+|too\s+"
    r"|all that\s+)?(?:hard|difficult|tough|a big deal|bad|an issue|a problem|a concern)"
    r"|(?:easily|comfortably)\s+(?:clear|pass|crack|get through)\s+(?:the\s+)?"
    r"(?:fmge|next|screening (?:exam|test))"
    r"|(?:clear|pass|crack)\s+(?:the\s+)?(?:fmge|next|screening (?:exam|test))\s+"
    r"(?:easily|no problem|first (?:try|attempt|go)|without (?:any\s+)?"
    r"(?:issues?|problems?|trouble))"
    r"|most\s+(?:students|people|of them)\s+(?:clear|pass|crack)\s+"
    r"(?:it|fmge|the fmge|next)\s+(?:easily|first (?:try|attempt|go)|without "
    r"(?:any\s+)?(?:issues?|problems?))"
    r"|(?:fmge|next)\s+pass\s+rate\s+is\s+(?:very|really|super|extremely)\s+high"
    r"|you(?:'ll| will)\s+(?:definitely\s+|easily\s+|for sure\s+|certainly\s+)?"
    r"(?:clear|pass|crack)\s+(?:the\s+)?(?:fmge|next|it)\b"
    # guaranteed career / outcome
    r"|you(?:'ll| will|'re| are)\s+(?:definitely\s+|certainly\s+|for sure\s+|100%\s+"
    r"|guaranteed to\s+)?(?:be|become)\s+a\s+doctor"
    r"|guaranteed\s+(?:career|future|job|success|placement|outcome)"
    r"|your\s+(?:career|future)\s+is\s+(?:secure|sorted|set|guaranteed|safe)\b"
    # minimized risk
    r"|(?:there(?:'s| is)\s+)?no\s+(?:real\s+)?risk\b(?!\s+of\b)"
    r"|zero\s+risk|risk[- ]free|nothing\s+(?:can|will|could)\s+go\s+wrong"
    r"|nothing\s+to\s+lose|you\s+have\s+nothing\s+to\s+lose|totally\s+safe\s+bet"
    r"|can'?t\s+go\s+wrong"
    # over-reassurance on safety
    r"|(?:100%|completely|totally|fully|absolutely|perfectly|entirely)\s+safe\b"
    r"|(?:completely|totally|absolutely)\s+(?:risk[- ]free|worry[- ]free|secure)"
    r"|you(?:'ll| will)\s+be\s+(?:totally|completely|perfectly|absolutely)\s+fine"
    r"|no\s+safety\s+(?:concerns?|issues?|worries?)\s+(?:at all|whatsoever)"
    r")",
    re.IGNORECASE,
)

_BARE_NOTHING_TO_WORRY = re.compile(r"nothing\s+to\s+worry\s+about", re.IGNORECASE)
_WORRY_CONTEXT = re.compile(
    r"\b(fmge|next|exam|screening|safe|safety|risk|admission|crime|security|"
    r"war|recogni[sz]\w*|degree|valid)\w*",
    re.IGNORECASE,
)


def find_overpromise(text: str) -> list[str]:
    """Phrases that promise an outcome or downplay a known difficulty."""

    hits = [m.group(0).strip() for m in _OVERPROMISE.finditer(text or "")]
    for m in _BARE_NOTHING_TO_WORRY.finditer(text or ""):
        window = text[max(0, m.start() - 80) : m.end() + 80]
        if _WORRY_CONTEXT.search(window):
            hits.append(m.group(0))
    seen: list[str] = []
    for h in hits:
        k = re.sub(r"\s+", " ", h.lower())
        if k not in seen:
            seen.append(k)
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


# --- cost-reply inclusions (round-2: never a bare figure) --------------

_COST_INCLUSION = re.compile(
    r"\b(visa|visas|passport|travel|flight|flights|airline|airfare|air ?ticket|"
    r"tickets?|accommodation|hostel|lodging|stay(?:ing)?|"
    r"food|mess|documentation|paperwork|"
    r"government institutes?|government[- ]led|"
    r"end[- ]to[- ]end|throughout (?:the |your )?(?:journey|stay|course)|"
    r"ongoing support|support (?:throughout|on the ground|there)|"
    r"we (?:handle|arrange|set up|assist with|help with|take care of)|"
    r"forwarding|pre[- ]?departure|post[- ]?arrival|settling in)\b",
    re.IGNORECASE,
)


def find_cost_inclusions(text: str) -> list[str]:
    """Concrete things a cost covers, so a figure is never stated bare."""

    return [m.group(0) for m in _COST_INCLUSION.finditer(text or "")]


# --- contact number presence (Georgia/Nepal cost replies) -------------

def reply_offers_number(text: str, phone: str) -> bool:
    """True when `phone` (digits, spacing-tolerant) appears in the reply, or a
    generic 'call <name> on ...' with any 10+-digit run. If no number is
    configured there is nothing to require, so returns True."""

    if not phone or not phone.strip():
        return True
    want = re.sub(r"\D", "", phone)
    have = re.sub(r"\D", "", text or "")
    if want and (want in have or want[-10:] in have):
        return True
    return bool(re.search(r"\b\+?\d[\d ()\-]{8,}\d\b", text or ""))


# --- countries named (which per-country range applies) ----------------

def countries_named(text: str, countries: list[str]) -> list[str]:
    """Subset of `countries` whose name appears in `text` (case-insensitive,
    word-bounded), in the order given."""

    lowered = (text or "").lower()
    return [
        c for c in countries
        if c.strip() and re.search(rf"\b{re.escape(c.strip().lower())}\b", lowered)
    ]


# --- premature contact/CTA offer (director review: enforced country-discussed
#     gate, not a prompt hint) ------------------------------------------

_CTA_PHRASES = re.compile(
    r"\b(quick call|short call|a call with|call him|call her|message him|"
    r"message her|reach out to him|reach out to her|book a call|schedule a "
    r"call|set up a call|office visit|visit (?:our|the) office|come (?:in|by|"
    r"see us)|walk in|in[- ]person meeting|15[- ]?min(?:ute)?s? call|"
    r"his number|her number|director'?s number|counsellor'?s number|"
    r"counselor'?s number)\b",
    re.IGNORECASE,
)


def find_counselor_offer(
    text: str,
    *,
    counselor_name: str = "",
    counselor_phone: str = "",
    office_address: str = "",
) -> list[str]:
    """Concrete signs a reply is offering the call / director's number / the
    office — the CTA content the "country discussed" gate withholds until a
    real country back-and-forth has happened. Deliberately narrow to actual
    contact-offer language (a phone number, the director by name, explicit
    call/meeting/office-visit phrasing) — an informational aside like "the
    counsellor confirms the exact figure" is not itself an offer and must not
    trip this."""

    t = text or ""
    hits: list[str] = []

    phone = (counselor_phone or "").strip()
    if phone:
        want = re.sub(r"\D", "", phone)
        have = re.sub(r"\D", "", t)
        if want and (want in have or want[-10:] in have):
            hits.append("phone number")

    name = (counselor_name or "").strip()
    if name:
        first = name.split()[0]
        if len(first) > 2 and re.search(rf"\b{re.escape(first)}\b", t, re.IGNORECASE):
            hits.append(name)
    if re.search(r"\brafique\s+sir\b", t, re.IGNORECASE):
        hits.append("Rafique Sir")

    address = (office_address or "").strip()
    if address:
        chunk = address.split(",")[0].strip()
        if len(chunk) > 3 and chunk.lower() in t.lower():
            hits.append("office address")

    hits += _CTA_PHRASES.findall(t)
    return hits
