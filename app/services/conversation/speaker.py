"""Per-turn student/parent detection.

Heuristic first (cheap, deterministic); an optional LLM classification can refine
it when the heuristic is unsure. The result is NEVER hard-locked for the thread
(plan §2) — it is recomputed every inbound message.
"""

from __future__ import annotations

import json
import re

from app.models.enums import RoleHint
from app.services.llm.base import LLMClient, LLMMessage

_PARENT_CUES = re.compile(
    r"\b(my (son|daughter|child|kid|beta|beti|ward)|as a (parent|father|mother)|"
    r"i am (his|her) (father|mother|parent|dad|mom|papa|mummy)|"
    r"mera beta|meri beti|hamara beta|bacche? ke liye|guardian)\b",
    re.IGNORECASE,
)
_STUDENT_CUES = re.compile(
    r"\b(i (scored|got|appeared|gave|wrote)|my (neet|rank|score|marks|percentile|"
    r"result|category|attempt)|i want to (study|become|do mbbs)|main ne diya|"
    r"mera score|meri rank|i'?m a student|12th|dropper)\b",
    re.IGNORECASE,
)


def heuristic_speaker(text: str) -> tuple[RoleHint, float]:
    if not text:
        return RoleHint.UNKNOWN, 0.0
    parent = bool(_PARENT_CUES.search(text))
    student = bool(_STUDENT_CUES.search(text))
    if parent and not student:
        return RoleHint.PARENT, 0.8
    if student and not parent:
        return RoleHint.STUDENT, 0.8
    if parent and student:
        return RoleHint.PARENT, 0.55  # a parent relaying the student's details
    return RoleHint.UNKNOWN, 0.0


async def detect_speaker(
    text: str,
    *,
    prior_role: RoleHint = RoleHint.UNKNOWN,
    llm: LLMClient | None = None,
    model: str | None = None,
    use_llm: bool = False,
) -> tuple[RoleHint, str]:
    role, confidence = heuristic_speaker(text)
    if confidence >= 0.7:
        return role, "heuristic"

    if use_llm and llm is not None and model and text.strip():
        try:
            resp = await llm.complete(
                system=(
                    "Classify who most likely wrote this WhatsApp message to an "
                    "MBBS-abroad consultancy: the prospective STUDENT, their PARENT, "
                    "or UNKNOWN. Reply as JSON: {\"speaker\": \"student|parent|unknown\"}."
                ),
                messages=[LLMMessage(role="user", content=text[:600])],
                model=model,
                max_output_tokens=40,
                json_mode=True,
                purpose="speaker",
            )
            data = json.loads(resp.text or "{}")
            value = str(data.get("speaker", "")).lower()
            if value in ("student", "parent", "unknown"):
                return RoleHint(value), "llm"
        except Exception:  # noqa: BLE001 - speaker detection never fails the turn
            pass

    if role != RoleHint.UNKNOWN:
        return role, "heuristic_weak"
    return (prior_role or RoleHint.UNKNOWN), "prior"
