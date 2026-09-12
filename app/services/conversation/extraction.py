"""In-conversation qualification extraction (sales-playbook Part 6).

Pulls the counsellor-useful facts out of the lead's own messages — NEET score,
category, city, target country, budget signal, intake urgency, parent-in-loop —
so the counsellor never re-asks on the call. Heuristics first (deterministic,
enough for the fake provider / tests); an optional LLM JSON pass fills the rest.

Nothing here is ever stated back by the bot as a "fact we told you"; these are
the lead's own statements, recorded on the lead row and the turn trace.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.config import Settings
from app.models.enums import LeadUrgency, NeetCategory, RoleHint
from app.models.lead import Lead
from app.services.eligibility import compute_eligibility
from app.services.llm.base import LLMClient, LLMMessage
from app.services.timeutils import utcnow

_SPECIFIC_RELAXED = frozenset(
    {NeetCategory.OBC, NeetCategory.SC, NeetCategory.ST, NeetCategory.EWS}
)

# NEET UG is out of 720. A stated score is realistically 1..720.
_NEET_MAX = 720

_SCORE_CUE = re.compile(
    r"\b(neet|score[ds]?|scoring|scored|marks?|got|mila|mile|percentile|"
    r"maine|mera|meri|main ne|nmbs)\b",
    re.IGNORECASE,
)
_CUTOFF_CUE = re.compile(
    r"\b(cut[- ]?off|cutoff|passing|qualifying|minimum|eligib|floor|threshold|"
    r"required|need to score|kitna chahiye)\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"(?<![\d.])(\d{1,3})(?![\d.])")

# Someone else's score, not the lead's — don't record it as theirs. NB: "son /
# daughter / child / beta / beti" are deliberately NOT here (a parent stating
# their child's score IS the qualifying score).
_THIRD_PARTY = re.compile(
    r"\b(friend|friends|friend'?s|cousin|cousin'?s|brother|sister|sibling|"
    r"classmate|batchmate|roommate|senior|junior|neighbou?r|"
    r"someone|somebody|some ?one|anyone|another (?:student|guy|girl|kid)|"
    r"dost|bhai|behen|yaar)\b",
    re.IGNORECASE,
)

# Category, in many phrasings. Ordered so specific tokens win over "reserved".
_CATEGORY_SPECIFIC = re.compile(
    r"(?<![a-z])("
    r"obc(?:[- ]?ncl)?|o\.?b\.?c\.?|other backward|"
    r"sc\b|s\.?c\.?|scheduled[- ]?caste|dalit|"
    r"st\b|s\.?t\.?|scheduled[- ]?tribe|adivasi|tribal|"
    r"ews|e\.?w\.?s\.?|economically weaker"
    r")(?![a-z])",
    re.IGNORECASE,
)
_CATEGORY_GENERAL = re.compile(
    r"(?<![a-z])("
    r"gen(?:eral)?(?:[- ]category)?|unreserved|un[- ]reserved|open(?:[- ]category)?|"
    r"samanya|no reservation|not reserved|non[- ]reserved|general merit"
    r")(?![a-z])",
    re.IGNORECASE,
)
# "reserved category", "I'm not general", "aarakshit", "quota", "SC/ST" (which
# of the relaxed ones is unstated). "not reserved" is NOT here — that's GENERAL.
_CATEGORY_RESERVED = re.compile(
    r"(?<![a-z])("
    r"(?<!not )(?<!non )reserved(?:[- ]category)?|aarakshit|arakshit|quota category|"
    r"(?:i'?m |i am |we'?re |belong to )?(?:not|non)[- ]?general|"
    r"sc[/ -]?st|st[/ -]?sc"
    r")(?![a-z])",
    re.IGNORECASE,
)

# Countries the KB / product actually talks about. Order matters (longest first).
_COUNTRIES: list[tuple[str, str]] = [
    ("united kingdom", "United Kingdom"),
    ("united states", "United States"),
    ("kazakhstan", "Kazakhstan"),
    ("uzbekistan", "Uzbekistan"),
    ("kyrgyzstan", "Kyrgyzstan"),
    ("georgia", "Georgia"),
    ("russia", "Russia"),
    ("armenia", "Armenia"),
    ("germany", "Germany"),
    ("bangladesh", "Bangladesh"),
    ("nepal", "Nepal"),
    ("philippines", "Philippines"),
    ("egypt", "Egypt"),
    ("uk", "United Kingdom"),
    ("usa", "United States"),
]
_COUNTRY_INTENT = re.compile(
    r"\b(want|interested|thinking|planning|prefer|looking at|go to|study in|"
    r"mbbs in|about|considering|option)\b",
    re.IGNORECASE,
)

_THIS_INTAKE = re.compile(
    r"\b(this year|this intake|current intake|2026|as soon as possible|asap|"
    r"immediately|right away|start soon|jaldi|abhi)\b",
    re.IGNORECASE,
)
_NEXT_INTAKE = re.compile(
    r"\b(next year|next intake|2027|after (?:a )?year|later intake|drop year)\b",
    re.IGNORECASE,
)
_UNDECIDED = re.compile(
    r"\b(just (?:exploring|checking|asking|looking)|not sure|undecided|"
    r"haven'?t decided|thinking about it|abhi soch)\b",
    re.IGNORECASE,
)

_BUDGET_TIGHT = re.compile(
    r"\b(tight budget|low budget|limited budget|cheap(?:est)?|economical|affordable|"
    r"budget hai kam|paisa kam|can'?t afford much|as low as possible)\b",
    re.IGNORECASE,
)
_BUDGET_FLEX = re.compile(
    r"(budget is (?:not|no|never)\s+(?:an? |any )?(?:issue|problem|bar|concern|constraint)|"
    r"flexible budget|money is (?:not|no)\s+(?:an? |any )?(?:issue|problem|bar)|"
    r"budget no bar|no budget (?:issue|problem|constraint)|"
    r"\bcan manage\b|whatever it takes)",
    re.IGNORECASE,
)
_BUDGET_FIGURE = re.compile(
    r"\bbudget\b[^.?!]{0,40}?(\d[\d,.]*\s?(?:lakhs?|lacs?|crores?|cr|l|k)\b)"
    r"|(\d[\d,.]*\s?(?:lakhs?|lacs?|crores?|cr|l)\b)[^.?!]{0,25}?\bbudget\b",
    re.IGNORECASE,
)

# PCB (Physics+Chemistry+Biology) percentage — the second, equally-required
# eligibility axis (director review). Requires the PCB / P-C-B cue explicitly,
# so it never collides with the NEET total-score extraction above.
_PCB_CUE = re.compile(
    r"\bpcb\b|physics.{0,20}chemistry.{0,20}biology|biology.{0,20}chemistry.{0,20}physics",
    re.IGNORECASE,
)
_PERCENT_NUM = re.compile(
    r"(\d{1,3}(?:\.\d+)?)\s*%"
    r"|(\d{1,3}(?:\.\d+)?)\s*(?:percent|percentage)\b"
    r"|(?:percent(?:age)?)\D{0,12}?(\d{1,3}(?:\.\d+)?)\b",
    re.IGNORECASE,
)

# India vs abroad interest (director review — cycle step 1, answers the fixed
# opening message). India-only checked first since it can itself contain the
# word "abroad" ("not interested in abroad").
_INDIA_ONLY_CUE = re.compile(
    r"\bonly\b[^.?!]{0,20}\bindia\b|\bindia\b[^.?!]{0,20}\bonly\b|"
    r"\bnot (?:interested in |going |planning )?abroad\b|"
    r"\bsirf india\b|\bstay\b[^.?!]{0,20}\bindia\b|\bwithin india\b|"
    r"\bindia (?:mein|main) hi\b",
    re.IGNORECASE,
)
_ABROAD_CUE = re.compile(
    r"\b(abroad|outside india|videsh|other countries?|overseas)\b",
    re.IGNORECASE,
)

# "Still deciding" on a country — a real answer to the country-decision cycle
# step, distinct from just not having named one yet (see
# Lead.country_still_deciding).
_COUNTRY_UNDECIDED = re.compile(
    r"\b(still deciding|haven'?t decided|not decided (?:yet|on a country)?|"
    r"not sure which country|which country (?:is best|should i|would you "
    r"recommend)|confused (?:between|about) countries|compare countries|"
    r"not sure about (?:the )?country|no idea which country)\b",
    re.IGNORECASE,
)


@dataclass
class QualifierExtraction:
    neet_score: int | None = None
    neet_category: NeetCategory | None = None
    pcb_percentage: float | None = None
    city: str | None = None
    target_country: str | None = None
    budget_band: str | None = None
    intake_year: int | None = None
    urgency: LeadUrgency | None = None
    parent_in_loop: bool = False
    # Cycle step 1 (director review): tri-state — None = not answered, True =
    # abroad (or open to it), False = India-only.
    considering_abroad: bool | None = None
    # Cycle step "country decision": a real "still deciding" answer, distinct
    # from simply not having named a country yet.
    country_still_deciding: bool = False
    methods: dict[str, str] = field(default_factory=dict)

    def any(self) -> bool:
        return any(
            v not in (None, False)
            for k, v in self.__dict__.items()
            if k != "methods"
        )


_LEAD_CUE_NEAR = re.compile(r"\b(i|maine|main ne|mera|meri|hum ?ne|humne)\b", re.IGNORECASE)


def _heuristic_score(text: str) -> int | None:
    if not _SCORE_CUE.search(text):
        return None
    for m in _NUMBER.finditer(text):
        val = int(m.group(1))
        if not (1 <= val <= _NEET_MAX):
            continue
        window = text[max(0, m.start() - 35) : m.end() + 25]
        near = text[max(0, m.start() - 16) : m.start()]  # the clause right before
        if _CUTOFF_CUE.search(window):
            continue  # they're asking about the cutoff, not stating their score
        if _THIRD_PARTY.search(near):
            continue  # "my friend got 180"
        if _THIRD_PARTY.search(window) and not _LEAD_CUE_NEAR.search(near):
            continue  # a friend was named and this number isn't clearly the lead's
        if val < 50 and not re.search(r"\b(scored?|got|marks?|neet)\b", window, re.IGNORECASE):
            continue
        return val
    return None


def _heuristic_pcb_percentage(text: str) -> float | None:
    if not _PCB_CUE.search(text):
        return None
    for m in _PERCENT_NUM.finditer(text):
        val = float(m.group(1) or m.group(2) or m.group(3))
        if not (0 <= val <= 100):
            continue
        near = text[max(0, m.start() - 16) : m.start()]
        window = text[max(0, m.start() - 35) : m.end() + 25]
        if _THIRD_PARTY.search(near):
            continue  # "my friend got 60% in PCB"
        if _THIRD_PARTY.search(window) and not _LEAD_CUE_NEAR.search(near):
            continue
        return val
    return None


def _heuristic_category(text: str) -> NeetCategory | None:
    # "SC/ST", "SC or ST" etc. → a relaxed category, but which is unstated.
    if re.search(r"(?<![a-z])(sc[/ -]?st|st[/ -]?sc|sc or st|st or sc)(?![a-z])", text, re.I):
        return NeetCategory.RESERVED
    m = _CATEGORY_SPECIFIC.search(text)
    if m:
        tok = re.sub(r"[^a-z]", "", m.group(1).lower())
        # distinctive words first — "scheduledtribe" also startswith "sc"
        if "tribe" in tok or tok in ("adivasi", "tribal"):
            return NeetCategory.ST
        if "caste" in tok or tok == "dalit":
            return NeetCategory.SC
        if "backward" in tok:
            return NeetCategory.OBC
        if "weaker" in tok:
            return NeetCategory.EWS
        if tok.startswith("obc"):
            return NeetCategory.OBC
        if tok.startswith("ews"):
            return NeetCategory.EWS
        if tok in ("sc", "scc"):  # "sc", "s.c."
            return NeetCategory.SC
        if tok in ("st", "stt"):
            return NeetCategory.ST
    # RESERVED is checked before GENERAL so "not general" / "non-general" don't
    # read as GENERAL.
    if _CATEGORY_RESERVED.search(text):
        return NeetCategory.RESERVED
    if _CATEGORY_GENERAL.search(text):
        return NeetCategory.GENERAL
    return None


def _heuristic_considering_abroad(text: str, country: str | None) -> bool | None:
    if _INDIA_ONLY_CUE.search(text):
        return False
    if country is not None or _ABROAD_CUE.search(text):
        return True
    return None


def _heuristic_country(text: str) -> str | None:
    lowered = text.lower()
    for needle, canonical in _COUNTRIES:
        if re.search(rf"\b{re.escape(needle)}\b", lowered):
            return canonical
    return None


def _heuristic_urgency(text: str) -> LeadUrgency | None:
    if _THIS_INTAKE.search(text):
        return LeadUrgency.THIS_INTAKE
    if _NEXT_INTAKE.search(text):
        return LeadUrgency.NEXT_INTAKE
    if _UNDECIDED.search(text):
        return LeadUrgency.UNDECIDED
    return None


def _heuristic_budget(text: str) -> str | None:
    m = _BUDGET_FIGURE.search(text)
    if m:
        return f"stated ~{(m.group(1) or m.group(2)).strip()}"
    if _BUDGET_FLEX.search(text):
        return "flexible"
    if _BUDGET_TIGHT.search(text):
        return "tight"
    return None


def heuristic_extract(text: str, *, speaker: RoleHint) -> QualifierExtraction:
    out = QualifierExtraction()
    score = _heuristic_score(text)
    if score is not None:
        out.neet_score = score
        out.methods["neet_score"] = "heuristic"
    cat = _heuristic_category(text)
    if cat is not None:
        out.neet_category = cat
        out.methods["neet_category"] = "heuristic"
    pcb = _heuristic_pcb_percentage(text)
    if pcb is not None:
        out.pcb_percentage = pcb
        out.methods["pcb_percentage"] = "heuristic"
    country = _heuristic_country(text)
    if country is not None:
        out.target_country = country
        out.methods["target_country"] = "heuristic"
    abroad = _heuristic_considering_abroad(text, country)
    if abroad is not None:
        out.considering_abroad = abroad
        out.methods["considering_abroad"] = "heuristic"
    if _COUNTRY_UNDECIDED.search(text):
        out.country_still_deciding = True
        out.methods["country_still_deciding"] = "heuristic"
    urg = _heuristic_urgency(text)
    if urg is not None:
        out.urgency = urg
        out.methods["urgency"] = "heuristic"
    budget = _heuristic_budget(text)
    if budget is not None:
        out.budget_band = budget
        out.methods["budget_band"] = "heuristic"
    if speaker == RoleHint.PARENT:
        out.parent_in_loop = True
        out.methods["parent_in_loop"] = "speaker"
    return out


_LLM_SYSTEM = (
    "You extract structured facts from a prospective MBBS-abroad lead's WhatsApp "
    "messages. Return ONLY facts the lead actually stated about THEMSELVES / their "
    "child — never guesses, never the bot's numbers. Reply as compact JSON with any "
    "of these keys you are sure about (omit the rest): "
    '{"neet_score": <int 1-720>, '
    '"neet_category": "general|obc|sc|st|ews|reserved", '
    '"pcb_percentage": <float 0-100>, '
    '"city": "<city or state>", "target_country": "<country>", '
    '"budget_band": "tight|flexible|stated ~<amount>", '
    '"intake_year": <int>, "urgency": "this_intake|next_intake|undecided", '
    '"parent_in_loop": true, "considering_abroad": true|false, '
    '"country_still_deciding": true}. '
    "If they correct or restate their score, report the corrected number. "
    "neet_category: 'general' for general/unreserved/open; the specific one if "
    "named (obc/sc/st/ews); 'reserved' if they say they are a reserved / "
    "non-general / quota category without saying which. A score for a friend or "
    "sibling is NOT the lead's — omit it. pcb_percentage is their percentage in "
    "Physics+Chemistry+Biology specifically (a separate figure from the NEET "
    "total score) — only report it if PCB/physics-chemistry-biology was named. "
    "considering_abroad: true if they said they're open to / interested in "
    "studying abroad (or named a specific country), false if they said they "
    "only want India — omit if not stated. country_still_deciding: true only "
    "if they explicitly said they haven't picked a country / want it compared "
    "for them — omit otherwise."
)


async def extract_qualifiers(
    text: str,
    *,
    speaker: RoleHint,
    llm: LLMClient | None = None,
    model: str | None = None,
    use_llm: bool = False,
) -> QualifierExtraction:
    out = heuristic_extract(text, speaker=speaker)

    if not (use_llm and llm is not None and model and text.strip()):
        return out

    try:
        resp = await llm.complete(
            system=_LLM_SYSTEM,
            messages=[LLMMessage(role="user", content=text[:1200])],
            model=model,
            max_output_tokens=160,
            json_mode=True,
            purpose="extraction",
        )
        data = json.loads(resp.text or "{}")
    except Exception:  # noqa: BLE001 - extraction never fails the turn
        return out

    if not isinstance(data, dict):
        return out

    def _fill(key: str, value: object, method: str = "llm") -> None:
        if value is not None and getattr(out, key) in (None, False):
            setattr(out, key, value)
            out.methods[key] = method

    score = data.get("neet_score")
    if isinstance(score, int) and 1 <= score <= _NEET_MAX:
        _fill("neet_score", score)
    cat = str(data.get("neet_category", "")).lower()
    if cat in {c.value for c in NeetCategory} and cat != "unknown":
        _fill("neet_category", NeetCategory(cat))
    pcb = data.get("pcb_percentage")
    if isinstance(pcb, (int, float)) and 0 <= pcb <= 100:
        _fill("pcb_percentage", float(pcb))
    if isinstance(data.get("city"), str) and data["city"].strip():
        _fill("city", data["city"].strip()[:120])
    if isinstance(data.get("target_country"), str) and data["target_country"].strip():
        _fill("target_country", data["target_country"].strip()[:80])
    if isinstance(data.get("budget_band"), str) and data["budget_band"].strip():
        _fill("budget_band", data["budget_band"].strip()[:60])
    if isinstance(data.get("intake_year"), int):
        _fill("intake_year", data["intake_year"])
    urg = str(data.get("urgency", "")).lower()
    if urg in {u.value for u in LeadUrgency} and urg != "unknown":
        _fill("urgency", LeadUrgency(urg))
    if data.get("parent_in_loop") is True and not out.parent_in_loop:
        out.parent_in_loop = True
        out.methods["parent_in_loop"] = "llm"
    abroad = data.get("considering_abroad")
    if isinstance(abroad, bool) and out.considering_abroad is None:
        out.considering_abroad = abroad
        out.methods["considering_abroad"] = "llm"
    if data.get("country_still_deciding") is True and not out.country_still_deciding:
        out.country_still_deciding = True
        out.methods["country_still_deciding"] = "llm"

    return out


def apply_to_lead(
    lead: Lead, extraction: QualifierExtraction, settings: Settings
) -> dict[str, object]:
    """Write extracted qualifiers onto the lead (non-destructively for the
    stable fields, last-stated-wins for the fluid ones). Returns the fields that
    actually changed, for the turn trace + counsellor visibility."""

    changed: dict[str, object] = {}

    def _fill_if_empty(attr: str, value: object, *, empty=(None,)) -> None:
        if value is None:
            return
        if getattr(lead, attr) in empty:
            setattr(lead, attr, value)
            changed[attr] = value

    def _update(attr: str, value: object) -> None:
        if value is not None and getattr(lead, attr) != value:
            setattr(lead, attr, value)
            changed[attr] = value

    # NEET score: last-stated-wins. The extractor only produces a score the lead
    # stated about themselves (first-person cue, third-party cue filtered), so a
    # later restatement ("actually I got 210, not 200") should take effect and
    # re-drive eligibility.
    _update("neet_score", extraction.neet_score)

    # PCB percentage: same pattern, same reason — a correction should re-drive
    # eligibility rather than stick with a stale figure.
    _update("pcb_percentage", extraction.pcb_percentage)

    # NEET category: last-stated-wins, but don't overwrite a specific relaxed
    # category (OBC/SC/ST/EWS) with the generic RESERVED — that's less
    # information, not a correction.
    new_cat = extraction.neet_category
    if new_cat is not None and new_cat is not lead.neet_category:
        downgrade = (
            new_cat is NeetCategory.RESERVED
            and lead.neet_category in _SPECIFIC_RELAXED
        )
        if not downgrade:
            lead.neet_category = new_cat
            changed["neet_category"] = new_cat.value

    _fill_if_empty("city", extraction.city)
    _fill_if_empty("intake_year", extraction.intake_year)
    _update("target_country", extraction.target_country)
    _update("budget_band", extraction.budget_band)
    # Cycle step 1 (director review): tri-state, so False (India-only) is a
    # real, meaningful answer — not "empty". Last-stated-wins, like the rest.
    _update("considering_abroad", extraction.considering_abroad)
    if extraction.country_still_deciding and not lead.country_still_deciding:
        lead.country_still_deciding = True
        changed["country_still_deciding"] = True
    if extraction.urgency is not None and lead.urgency != extraction.urgency:
        lead.urgency = extraction.urgency
        changed["urgency"] = extraction.urgency.value
    if extraction.parent_in_loop and not lead.parent_in_loop:
        lead.parent_in_loop = True
        changed["parent_in_loop"] = True

    # Eligibility is a pure function of (score, category, PCB%, cutoffs).
    # Recompute it unconditionally so it can never drift from its inputs and so
    # statement order never matters. NEEDS_CATEGORY / NEEDS_PCB persist here
    # until the lead answers.
    new_flag = compute_eligibility(
        lead.neet_score, lead.neet_category, lead.pcb_percentage, settings
    )
    if new_flag != lead.eligibility_flag:
        lead.eligibility_flag = new_flag
        changed["eligibility_flag"] = new_flag.value

    if changed:
        lead.qualifiers_updated_at = utcnow()
    return changed
