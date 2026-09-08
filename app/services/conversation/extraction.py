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

_CATEGORY = re.compile(
    r"\b(general|gen(?:eral)?\s+category|unreserved|open category|obc(?:[- ]ncl)?|"
    r"\bsc\b|\bst\b|\bews\b)\b",
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


@dataclass
class QualifierExtraction:
    neet_score: int | None = None
    neet_category: NeetCategory | None = None
    city: str | None = None
    target_country: str | None = None
    budget_band: str | None = None
    intake_year: int | None = None
    urgency: LeadUrgency | None = None
    parent_in_loop: bool = False
    methods: dict[str, str] = field(default_factory=dict)

    def any(self) -> bool:
        return any(
            v not in (None, False)
            for k, v in self.__dict__.items()
            if k != "methods"
        )


def _heuristic_score(text: str) -> int | None:
    if not _SCORE_CUE.search(text):
        return None
    for m in _NUMBER.finditer(text):
        val = int(m.group(1))
        if not (1 <= val <= _NEET_MAX):
            continue
        window = text[max(0, m.start() - 25) : m.end() + 25]
        if _CUTOFF_CUE.search(window):
            continue  # they're asking about the cutoff, not stating their score
        if val < 50 and not re.search(r"\b(scored?|got|marks?|neet)\b", window, re.IGNORECASE):
            continue
        return val
    return None


def _heuristic_category(text: str) -> NeetCategory | None:
    m = _CATEGORY.search(text)
    if not m:
        return None
    token = re.sub(r"\s+", " ", m.group(0).lower()).strip()
    if token.startswith(("general", "gen", "unreserved", "open")):
        return NeetCategory.GENERAL
    if token.startswith("obc"):
        return NeetCategory.OBC
    if token == "sc":
        return NeetCategory.SC
    if token == "st":
        return NeetCategory.ST
    if token == "ews":
        return NeetCategory.EWS
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
    country = _heuristic_country(text)
    if country is not None:
        out.target_country = country
        out.methods["target_country"] = "heuristic"
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
    '{"neet_score": <int 1-720>, "neet_category": "general|obc|sc|st|ews", '
    '"city": "<city or state>", "target_country": "<country>", '
    '"budget_band": "tight|flexible|stated ~<amount>", '
    '"intake_year": <int>, "urgency": "this_intake|next_intake|undecided", '
    '"parent_in_loop": true}'
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

    _fill_if_empty("neet_score", extraction.neet_score)
    if extraction.neet_category is not None and lead.neet_category in (
        None,
        NeetCategory.UNKNOWN,
    ):
        lead.neet_category = extraction.neet_category
        changed["neet_category"] = extraction.neet_category.value
    _fill_if_empty("city", extraction.city)
    _fill_if_empty("intake_year", extraction.intake_year)
    _update("target_country", extraction.target_country)
    _update("budget_band", extraction.budget_band)
    if extraction.urgency is not None and lead.urgency != extraction.urgency:
        lead.urgency = extraction.urgency
        changed["urgency"] = extraction.urgency.value
    if extraction.parent_in_loop and not lead.parent_in_loop:
        lead.parent_in_loop = True
        changed["parent_in_loop"] = True

    if {"neet_score", "neet_category"} & changed.keys():
        new_flag = compute_eligibility(lead.neet_score, lead.neet_category, settings)
        if new_flag != lead.eligibility_flag:
            lead.eligibility_flag = new_flag
            changed["eligibility_flag"] = new_flag.value

    if changed:
        lead.qualifiers_updated_at = utcnow()
    return changed
