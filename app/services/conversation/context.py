"""Assemble everything one conversation turn needs for the LLM + the guard."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.enums import MessageDirection, MessageType, RoleHint
from app.models.lead import Lead
from app.models.message import Message
from app.services.conversation.prompt import render_system_prompt
from app.services.guard.guard import GuardContext
from app.services.knowledge.base import KBChunk, KnowledgeBase
from app.services.llm.base import LLMMessage

_HISTORY_TYPES = {
    MessageType.TEXT,
    MessageType.TEMPLATE,
    MessageType.INTERACTIVE,
    MessageType.BUTTON,
}


@dataclass
class TurnContext:
    system_prompt: str
    messages: list[LLMMessage]
    kb_chunks: list[KBChunk]
    guard_context: GuardContext
    speaker: RoleHint
    engagement_phase: str
    profile_summary: str
    recent_text: str = ""
    extras: dict = field(default_factory=dict)


def _profile_summary(lead: Lead) -> str:
    parts: list[str] = []
    if lead.full_name:
        parts.append(f"name={lead.full_name}")
    if lead.city:
        parts.append(f"city={lead.city}")
    if lead.language_preference:
        parts.append(f"language={lead.language_preference}")
    if lead.neet_score is not None:
        parts.append(f"neet_score={lead.neet_score}")
    if lead.neet_category and lead.neet_category.value != "unknown":
        parts.append(f"neet_category={lead.neet_category.value}")
    if lead.target_country:
        parts.append(f"target_country={lead.target_country}")
    if lead.budget_band:
        parts.append(f"budget_band={lead.budget_band}")
    if lead.intake_year:
        parts.append(f"intake_year={lead.intake_year}")
    if lead.urgency and lead.urgency.value != "unknown":
        parts.append(f"urgency={lead.urgency.value}")
    if lead.parent_in_loop:
        parts.append("parent_in_loop=yes")
    return "; ".join(parts) or "nothing stated yet"


async def _history(
    session: AsyncSession, lead: Lead, *, limit: int
) -> list[Message]:
    rows = (
        await session.scalars(
            select(Message)
            .where(Message.lead_id == lead.id)
            .where(Message.direction.in_([MessageDirection.INBOUND, MessageDirection.OUTBOUND]))
            .order_by(Message.created_at.desc())
            .limit(limit * 2)
        )
    ).all()
    rows = [
        m
        for m in rows
        if m.message_type in _HISTORY_TYPES and (m.body or m.template_name)
    ]
    return list(reversed(rows))[-limit:]


def _to_llm_messages(history: list[Message]) -> list[LLMMessage]:
    out: list[LLMMessage] = []
    for m in history:
        content = m.body or f"(template: {m.template_name})"
        role = "user" if m.direction == MessageDirection.INBOUND else "assistant"
        out.append(LLMMessage(role=role, content=content))
    # the model requires the first message to be from the user
    while out and out[0].role != "user":
        out.pop(0)
    return out


async def build_turn_context(
    session: AsyncSession,
    lead: Lead,
    *,
    settings: Settings,
    kb: KnowledgeBase,
    speaker: RoleHint,
    speaker_method: str,
    latest_text: str,
) -> TurnContext:
    engagement_phase = lead.engagement_phase()
    history = await _history(session, lead, limit=settings.conversation_history_turns)
    llm_messages = _to_llm_messages(history)
    if not llm_messages or llm_messages[-1].content != latest_text:
        llm_messages.append(LLMMessage(role="user", content=latest_text))

    kb_chunks = kb.retrieve(latest_text, k=settings.kb_retrieval_k)
    kb_block = "\n".join(f"[{c.id}] {c.title} — {c.text.strip()}" for c in kb_chunks) or "(none)"

    profile = _profile_summary(lead)
    financing = "yes" if lead.financing_cleared else "no"

    turn_block = (
        "\n\n## Current turn context\n"
        f"speaker: {speaker.value} (via {speaker_method})\n"
        f"engagement_phase: {engagement_phase}\n"
        f"lifecycle_state: {lead.lifecycle_state.value}\n"
        f"eligibility_flag: {lead.eligibility_flag.value}\n"
        f"known_profile: {profile}\n"
        f"financing_cleared: {financing}\n"
        "\n## Knowledge snippets (rely on these; do not add facts beyond them)\n"
        f"{kb_block}\n"
    )
    system_prompt = render_system_prompt(settings) + turn_block

    recent_text = "\n".join(m.content for m in llm_messages[-6:])
    guard_context = GuardContext(
        conversation_text=recent_text,
        financing_cleared=lead.financing_cleared,
        stateable_range=settings.stateable_cost_range,
        india_compare_range=settings.india_compare_cost_range,
        premium_countries=settings.premium_cost_country_list,
        stateable_countries=settings.stateable_cost_country_list,
    )

    return TurnContext(
        system_prompt=system_prompt,
        messages=llm_messages,
        kb_chunks=kb_chunks,
        guard_context=guard_context,
        speaker=speaker,
        engagement_phase=engagement_phase,
        profile_summary=profile,
        recent_text=recent_text,
    )
