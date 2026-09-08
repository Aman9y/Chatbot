"""Detect when the lead has agreed to a call / meeting.

Heuristic + optional LLM confirmation. A booking triggers BOOKING_CONFIRMED ->
HANDOFF and a counsellor notification, so the detector is conservative: it fires
only on a clear acceptance, not on "maybe later".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.services.llm.base import LLMClient, LLMMessage

_ACCEPT = re.compile(
    r"\b(ok(ay)? (call|book|schedule|let'?s)|yes,? (call|book|please call|let'?s|do)|"
    r"call me (tomorrow|today|on|at|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|morning|evening|afternoon|night)|"
    r"book (the|a) (call|meeting|slot)|let'?s (do|book|schedule) (the|a|it)|"
    r"i'?m free (tomorrow|today|on|at|this)|"
    r"call kar(o|na)|call kar sakte|baat karte hain|meeting fix|"
    r"schedule (the|a|it)|works for me|that works|sounds good,? (call|book))\b",
    re.IGNORECASE,
)
_TIME_HINT = re.compile(
    r"\b(tomorrow|today|tonight|monday|tuesday|wednesday|thursday|friday|saturday|"
    r"sunday|morning|afternoon|evening|\d{1,2}\s?(am|pm|:\d{2})|"
    r"after \d|before \d|this week|next week|weekend)\b",
    re.IGNORECASE,
)
_OFFICE = re.compile(
    r"\b(office|in person|in-person|come (over|there)|visit|meet you)\b", re.IGNORECASE
)


@dataclass
class BookingSignal:
    detected: bool
    method: str = "none"
    proposed_time: str | None = None
    format: str | None = None  # "call" | "office"


def heuristic_booking(text: str) -> BookingSignal:
    if not text or not _ACCEPT.search(text):
        return BookingSignal(detected=False)
    time_match = _TIME_HINT.search(text)
    fmt = "office" if _OFFICE.search(text) else "call"
    return BookingSignal(
        detected=True,
        method="heuristic",
        proposed_time=time_match.group(0) if time_match else None,
        format=fmt,
    )


async def detect_booking(
    recent_turns: list[LLMMessage],
    *,
    llm: LLMClient | None = None,
    model: str | None = None,
    use_llm: bool = True,
) -> BookingSignal:
    last_user = next((m.content for m in reversed(recent_turns) if m.role == "user"), "")
    heuristic = heuristic_booking(last_user)
    if heuristic.detected:
        return heuristic

    if not (use_llm and llm is not None and model and last_user.strip()):
        return BookingSignal(detected=False)

    transcript = "\n".join(f"{m.role}: {m.content}" for m in recent_turns[-6:])
    try:
        resp = await llm.complete(
            system=(
                "You read a short WhatsApp transcript between an MBBS-abroad "
                "consultancy bot and a lead. Decide if, in the latest lead message, "
                "the lead has clearly AGREED to a phone call or office meeting with "
                "the counsellor (not just expressed vague interest). "
                'Reply JSON: {"booking": true|false, "time": "<text or null>", '
                '"format": "call|office|null"}.'
            ),
            messages=[LLMMessage(role="user", content=transcript[:1500])],
            model=model,
            max_output_tokens=60,
            json_mode=True,
            purpose="booking",
        )
        data = json.loads(resp.text or "{}")
        if bool(data.get("booking")):
            fmt = data.get("format")
            return BookingSignal(
                detected=True,
                method="llm",
                proposed_time=(data.get("time") or None),
                format=fmt if fmt in ("call", "office") else "call",
            )
    except Exception:  # noqa: BLE001 - booking detection never fails the turn
        pass
    return BookingSignal(detected=False)
