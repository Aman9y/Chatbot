"""Phone normalization to E.164, with India-friendly pre-cleaning."""

from __future__ import annotations

import re
from dataclasses import dataclass

import phonenumbers

from app.errors import PhoneNormalizationError

_NON_DIAL = re.compile(r"[^\d+]")


@dataclass(frozen=True)
class NormalizedPhone:
    e164: str
    country: str | None
    national: str
    raw: str


def _preclean(raw: str, default_region: str) -> str:
    """Best-effort cleanup of the messy formats seen in legacy lead lists."""

    s = raw.strip()
    s = s.replace("(", "").replace(")", "")
    # keep a single leading +, drop all other non-digits
    plus = s.startswith("+")
    s = _NON_DIAL.sub("", s)
    if plus:
        s = "+" + s.lstrip("+")

    if s.startswith("+"):
        return s

    digits = s
    if default_region.upper() == "IN":
        if len(digits) == 12 and digits.startswith("91"):
            return "+" + digits
        if len(digits) == 11 and digits.startswith("0"):
            return "+91" + digits[1:]
        if len(digits) == 10 and digits[0] in "6789":
            return "+91" + digits
        if len(digits) == 13 and digits.startswith("091"):
            return "+" + digits[1:]
    return digits


def normalize_phone(raw: str | None, default_region: str = "IN") -> NormalizedPhone:
    if raw is None or not str(raw).strip():
        raise PhoneNormalizationError("empty phone number")

    original = str(raw).strip()
    candidate = _preclean(original, default_region)

    try:
        parsed = phonenumbers.parse(
            candidate,
            None if candidate.startswith("+") else default_region.upper(),
        )
    except phonenumbers.NumberParseException as exc:  # pragma: no cover - passthrough
        raise PhoneNormalizationError(f"cannot parse {original!r}: {exc}") from exc

    if not phonenumbers.is_valid_number(parsed):
        raise PhoneNormalizationError(f"not a valid phone number: {original!r}")

    return NormalizedPhone(
        e164=phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164),
        country=phonenumbers.region_code_for_number(parsed),
        national=str(parsed.national_number),
        raw=original,
    )


def normalize_wa_id(wa_id: str, default_region: str = "IN") -> NormalizedPhone:
    """Meta sends sender/recipient as bare digits (e.g. '919812345678')."""

    wa_id = wa_id.strip()
    if not wa_id.startswith("+"):
        wa_id = "+" + wa_id
    return normalize_phone(wa_id, default_region)
