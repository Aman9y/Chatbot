"""Parse Indian-format money figures and ranges into a number of lakh.

Pure, no app imports. The config turns the confirmed per-country range strings
into numeric bounds; the Response Guard checks a figure in an LLM reply against
the approved bound for the country in scope.

  to_lakh("40 lakh")           -> 40.0
  to_lakh("1.2 crore")         -> 120.0
  to_lakh("Rs 45,00,000")      -> 45.0
  to_lakh("forty lakh")        -> 40.0
  range_to_lakh("Rs 30-35 lakh")   -> (30.0, 35.0)
  range_to_lakh("Rs 80L-1.2Cr")    -> (80.0, 120.0)

``to_lakh`` takes ONE figure (one item from ``find_money``), not a range.
"""

from __future__ import annotations

import re

_UNITS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}

# "cr" / "l" attach straight to a digit ("80L", "1.2Cr"), so a plain \b before
# them is not enough — require a non-letter (digit/space/symbol) to the left.
_CRORE = re.compile(r"crores?\b|(?<![a-z])cr\b", re.IGNORECASE)
_LAKH = re.compile(r"lakhs?\b|lacs?\b|(?<![a-z])lac\b|(?<![a-z])l\b", re.IGNORECASE)
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_TENS_RE = re.compile(
    r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
    r"(?:[-\s]+(one|two|three|four|five|six|seven|eight|nine))?\b",
    re.IGNORECASE,
)
_SMALL_RE = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen)\b",
    re.IGNORECASE,
)


def _word_to_int(s: str) -> int | None:
    m = _TENS_RE.search(s)
    if m:
        total = _TENS[m.group(1).lower()]
        if m.group(2):
            total += _UNITS[m.group(2).lower()]
        return total
    m = _SMALL_RE.search(s)
    return _UNITS[m.group(1).lower()] if m else None


def _scale(text: str) -> float:
    """crore -> 100, lakh -> 1, unknown -> 0."""
    if _CRORE.search(text):
        return 100.0
    if _LAKH.search(text):
        return 1.0
    return 0.0


def to_lakh(text: str) -> float | None:
    if not text:
        return None
    t = text.strip()
    scale = _scale(t)

    m = _NUM.search(t)
    if m:
        try:
            value = float(m.group(0).replace(",", ""))
        except ValueError:
            return None
        if scale:
            return value * scale
        # bare digits are only a money amount when they are a full rupee figure
        return value / 100_000 if value >= 100_000 else None

    n = _word_to_int(t)
    if n is None or not scale:
        return None
    return n * scale


def range_to_lakh(text: str) -> tuple[float, float] | None:
    if not text:
        return None
    parts = re.split(r"\s*(?:–|—|-|to|through)\s*", text.strip(), maxsplit=1)
    if len(parts) != 2:
        one = to_lakh(text)
        return (one, one) if one is not None else None

    left, right = parts
    left_scale = _scale(left)
    right_scale = _scale(right)
    # each side inherits the other's scale word when it lacks its own
    left_scale = left_scale or right_scale
    right_scale = right_scale or left_scale
    if not left_scale or not right_scale:
        return None

    lo = _bare_value(left)
    hi = _bare_value(right)
    if lo is None or hi is None:
        return None
    lo, hi = lo * left_scale, hi * right_scale
    return (min(lo, hi), max(lo, hi))


def _bare_value(side: str) -> float | None:
    m = _NUM.search(side)
    if m:
        try:
            return float(m.group(0).replace(",", ""))
        except ValueError:
            return None
    w = _word_to_int(side)
    return float(w) if w is not None else None


# range spans like "₹30–35 lakh" / "27-45L" / "₹80L – 1.2 Cr"
_RANGE_SPAN = re.compile(
    r"(?:₹|rs\.?|inr)?\s*\d[\d,]*(?:\.\d+)?\s*(?:lakhs?|lacs?|lac|crores?|cr|l)?"
    r"\s*(?:–|—|-|to)\s*"
    r"(?:₹|rs\.?|inr)?\s*\d[\d,]*(?:\.\d+)?\s*(?:lakhs?|lacs?|lac|crores?|cr|l)\b",
    re.IGNORECASE,
)


def has_range_span(text: str) -> bool:
    """True if `text` contains a written range span like '₹30–35 lakh'."""
    return bool(_RANGE_SPAN.search(text or ""))


def range_spans_in_lakh(text: str) -> list[tuple[float, float]]:
    """(low, high) in lakh for every written range span in `text`."""
    out: list[tuple[float, float]] = []
    for m in _RANGE_SPAN.finditer(text or ""):
        bounds = range_to_lakh(m.group(0))
        if bounds:
            out.append(bounds)
    return out


def figures_in_lakh(text: str) -> list[float]:
    """Every money amount in `text`, as lakh. Range spans yield BOTH endpoints;
    the rest are parsed individually. A currency-marked bare number with no
    scale word ("₹30" in "₹30–35 lakh" leftovers) is read as lakh.
    """
    from app.services.guard import detectors  # local: avoid import cycle

    out: list[float] = []
    rest = text or ""
    for m in _RANGE_SPAN.finditer(text or ""):
        bounds = range_to_lakh(m.group(0))
        if bounds:
            out.extend(bounds)
        rest = rest.replace(m.group(0), " ", 1)

    for raw in detectors.find_money(rest):
        v = to_lakh(raw)
        if v is None and re.search(r"(?:₹|rs\.?|inr)\s*\d", raw, re.IGNORECASE):
            # currency + bare number, no scale -> lakh in a cost context
            n = _NUM.search(raw)
            if n:
                try:
                    out.append(float(n.group(0).replace(",", "")))
                except ValueError:
                    pass
        elif v is not None:
            out.append(v)
        else:
            out.append(float("nan"))  # unparseable -> force a range failure
    return out
