"""Opt-out ("STOP") keyword detection.

Runs *before* any normal message processing (critique A3). The matcher is
deliberately biased toward honoring an opt-out: a borderline message is treated
as an opt-out rather than risk continuing to contact someone who asked to stop.
"""

from __future__ import annotations

import re
import unicodedata

# Unambiguous single tokens — matched even inside a longer sentence.
_STRONG = {
    "stop",
    "unsubscribe",
    "optout",
    "opt-out",
    "stopall",
    "cancel",
}

DEFAULT_STOP_KEYWORDS: list[str] = [
    # English
    "stop",
    "stop promotions",
    "stop promotion",
    "unsubscribe",
    "opt out",
    "optout",
    "remove me",
    "remove my number",
    "do not message",
    "dont message",
    "don't message",
    "do not contact",
    "leave me alone",
    "no more messages",
    "quit",
    # Hindi (romanized)
    "band karo",
    "band kro",
    "band kar do",
    "message band",
    "message mat bhejo",
    "mat bhejo",
    "mujhe mat",
    "hata do",
    "number hata do",
    "pareshan mat karo",
    # Hindi (Devanagari)
    "बंद करो",
    "बंद करें",
    "मैसेज बंद",
    "मत भेजो",
    "मुझे मत",
    "हटा दो",
    "परेशान मत करो",
]

_SENTINEL = "<defaults>"
_WS = re.compile(r"\s+")


def load_keywords(raw: str | None) -> list[str]:
    if raw is None or not raw.strip():
        return list(DEFAULT_STOP_KEYWORDS)
    parts: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if token == _SENTINEL:
            parts.extend(DEFAULT_STOP_KEYWORDS)
        else:
            parts.append(token.lower())
    return parts or list(DEFAULT_STOP_KEYWORDS)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().strip()
    text = text.strip(".,!?;:\"'()[]{}<>-–—…")
    return _WS.sub(" ", text)


def is_opt_out(text: str | None, keywords: list[str]) -> bool:
    if not text:
        return False
    norm = _normalize(text)
    if not norm:
        return False
    words = norm.split()

    # 1. exact match on any keyword
    for kw in keywords:
        if norm == _normalize(kw):
            return True

    # 2. short messages (<= 6 words): any keyword appearing as substring / word
    if len(words) <= 6:
        for kw in keywords:
            k = _normalize(kw)
            if " " in k and k in norm:
                return True
            if k in words:
                return True

    # 3. any-length message: an unambiguous strong token present as a word,
    #    or the message starts with one.
    if words and words[0] in _STRONG:
        return True
    if _STRONG.intersection(words) and len(words) <= 10:
        return True

    return False
