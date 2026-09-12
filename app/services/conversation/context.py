"""Assemble everything one conversation turn needs for the LLM + the guard."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.conversation_trace import ConversationTrace
from app.models.enums import (
    EligibilityFlag,
    MessageDirection,
    MessageType,
    RoleHint,
)
from app.models.lead import Lead
from app.models.message import Message
from app.services.conversation.deflection import DeflectionPlan, select_deflection, turn_hint
from app.services.conversation.extraction import heuristic_extract
from app.services.conversation.pacing import PacePlan, plan_pace
from app.services.conversation.prompt import render_system_prompt
from app.services.conversation.triage import (
    Objection,
    TopicMatch,
    classify_topic,
    detect_objection,
)
from app.services.guard import detectors
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
    pace_plan: PacePlan | None = None
    topic_match: TopicMatch | None = None
    objection: Objection | None = None
    deflection: DeflectionPlan | None = None
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
    if lead.pcb_percentage is not None:
        parts.append(f"pcb_percentage={lead.pcb_percentage:g}%")
    if lead.considering_abroad is not None:
        parts.append(f"considering_abroad={lead.considering_abroad}")
    if lead.target_country:
        parts.append(f"target_country={lead.target_country}")
    elif lead.country_still_deciding:
        parts.append("country_still_deciding=yes")
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


_FRUSTRATED = re.compile(
    r"\b(you keep saying|same (?:answer|thing)|stop repeating|not helpful|"
    r"useless|waste of (?:my )?time|just answer|answer the question|"
    r"why (?:can'?t|won'?t) you|are you even|this is (?:annoying|frustrating)|"
    r"frustrat\w*)\b|\?!|!!",
    re.IGNORECASE,
)

_WORD = re.compile(r"[a-z0-9]+")
_REPEAT_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "is", "are",
    "i", "my", "me", "you", "your", "we", "it", "this", "that", "do", "does",
    "can", "what", "how", "please", "tell", "just", "so", "if", "will", "be",
}


def _content_tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall((text or "").lower()) if t not in _REPEAT_STOP and len(t) > 2}


def _looks_like_repeat(latest: str, prior_user_texts: list[str]) -> bool:
    cur = _content_tokens(latest)
    if len(cur) < 2:
        return False
    for prev in prior_user_texts:
        pv = _content_tokens(prev)
        if not pv:
            continue
        overlap = len(cur & pv)
        if overlap >= 2 and overlap / min(len(cur), len(pv)) >= 0.6:
            return True
    return False


async def _deflection_history(
    session: AsyncSession, lead: Lead, *, limit: int = 6
) -> tuple[int, int | None]:
    """(count of prior bot deflections in this conversation, the most recent mode)."""

    rows = (
        await session.scalars(
            select(ConversationTrace.turn_signals)
            .where(ConversationTrace.lead_id == lead.id)
            .where(ConversationTrace.final_action.in_(["sent", "fallback_sent"]))
            .order_by(ConversationTrace.created_at.desc())
            .limit(limit)
        )
    ).all()
    count = 0
    last_mode: int | None = None
    for sig in rows:
        d = (sig or {}).get("deflection") if isinstance(sig, dict) else None
        if not d:
            continue
        count += 1
        if last_mode is None:
            last_mode = d.get("mode")
    return count, last_mode


async def build_turn_context(
    session: AsyncSession,
    lead: Lead,
    *,
    settings: Settings,
    kb: KnowledgeBase,
    speaker: RoleHint,
    speaker_method: str,
    latest_text: str,
    minutes_since_last_bot: float | None = None,
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

    message_depth = sum(1 for m in llm_messages if m.role == "user")
    topic_match = classify_topic(latest_text)
    objection = detect_objection(latest_text)

    prior_user_texts = [m.content for m in llm_messages[:-1] if m.role == "user"]
    prior_bot_texts = [m.content for m in llm_messages if m.role == "assistant"]

    # Enforced state (director review), not a prompt hint: a real country
    # back-and-forth has happened once BOTH sides have actually named an
    # approved country somewhere in this conversation — the lead raising one
    # and the bot responding with one. Sticky: only ever flips false -> true,
    # recomputed from the real history every turn so it can never depend on a
    # prompt instruction being followed. The Response Guard enforces it (see
    # app/services/guard/guard.py:_check_premature_contact_offer).
    if not lead.country_discussed:
        approved_countries = list(settings.country_cost_bounds)
        lead_side = " ".join((*prior_user_texts, latest_text))
        bot_side = "\n".join(prior_bot_texts)
        if detectors.countries_named(lead_side, approved_countries) and detectors.countries_named(
            bot_side, approved_countries
        ):
            lead.country_discussed = True

    # Sticky, computed from real history exactly like country_discussed: has
    # the bot actually introduced Rafique Sir (by name/number) at least once
    # already? Decides whether the next mention needs the full first-time
    # framing or just a soft, engagement-paced resurfacing.
    if not lead.rafique_introduced and detectors.find_counselor_offer(
        "\n".join(prior_bot_texts),
        counselor_name=settings.counselor_name,
        counselor_phone=settings.counselor_phone,
        office_address=settings.office_address,
    ):
        lead.rafique_introduced = True

    # The guard's own effective country-discussed state (below-cutoff leads are
    # exempt from the gate — see the GuardContext construction below). Reused
    # here so the per-turn cycle note never contradicts what the guard will
    # actually allow.
    effective_country_discussed = (
        lead.country_discussed or lead.eligibility_flag is EligibilityFlag.BELOW_CUTOFF
    )

    # Engagement signal (director review): the number should surface on genuine
    # engagement — a real question or follow-through, not a fixed message count.
    # A turn counts as substantive if it matched a real topic (not just small
    # talk) OR it gave us a qualifier we asked for (score, category, country,
    # budget, ...) — i.e. "moving past surface-level chat" either direction.
    substantive_depth = sum(
        1
        for t in (*prior_user_texts, latest_text)
        if classify_topic(t) is not None or heuristic_extract(t, speaker=speaker).any()
    )
    pace_plan = plan_pace(
        message_depth=message_depth,
        minutes_since_last_bot=minutes_since_last_bot,
        engagement_phase=engagement_phase,
        substantive_depth=substantive_depth,
    )
    deflect_count, last_mode = await _deflection_history(session, lead)
    repeat = _looks_like_repeat(latest_text, prior_user_texts)
    frustrated = bool(_FRUSTRATED.search(latest_text))
    deflection = select_deflection(
        topic_match=topic_match,
        objection=objection,
        speaker=speaker,
        deflect_index=deflect_count,
        repeat_detected=repeat,
        last_mode=last_mode,
        frustrated=frustrated,
    )

    turn_block = (
        "\n\n## Current turn context\n"
        f"speaker: {speaker.value} (via {speaker_method})\n"
        f"engagement_phase: {engagement_phase}\n"
        f"lifecycle_state: {lead.lifecycle_state.value}\n"
        f"eligibility_flag: {lead.eligibility_flag.value}\n"
        f"known_profile: {profile}\n"
        f"financing_cleared: {financing}\n"
        f"{_pace_block(pace_plan)}"
        f"{_country_gate_block(effective_country_discussed)}"
        f"{_cycle_block(lead, effective_country_discussed)}"
        f"{_topic_block(topic_match)}"
        f"{_objection_block(objection)}"
        f"{_deflection_block(deflection, settings)}"
        "\n## Knowledge snippets (rely on these; do not add facts beyond them)\n"
        f"{kb_block}\n"
    )
    system_prompt = render_system_prompt(settings) + turn_block

    recent_text = "\n".join(m.content for m in llm_messages[-6:])
    guard_context = GuardContext(
        conversation_text=recent_text,
        lead_message=latest_text,
        prior_bot_text="\n".join(prior_bot_texts[-4:]),
        financing_cleared=lead.financing_cleared,
        country_bounds=settings.country_cost_bounds,
        country_display=settings.country_cost_range_display,
        india_compare_bounds=settings.india_compare_bounds,
        india_compare_display=settings.india_compare_cost_range,
        premium_countries=settings.premium_cost_country_list,
        sensitive_countries=settings.sensitive_cost_country_list,
        counselor_phone=settings.counselor_phone,
        counselor_name=settings.counselor_name,
        office_address=settings.office_address,
        country_discussed=effective_country_discussed,
        # FIX 3 (director review): structural backstop against re-asking an
        # already-answered cycle-step question — see
        # app/services/guard/guard.py:_check_redundant_question.
        neet_score_known=lead.neet_score is not None,
        pcb_percentage_known=lead.pcb_percentage is not None,
        interest_known=lead.considering_abroad is not None,
        country_decision_known=bool(lead.target_country) or lead.country_still_deciding,
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
        pace_plan=pace_plan,
        topic_match=topic_match,
        objection=objection,
        deflection=deflection,
    )


_HANDLING_LABEL = {
    "FULL": "FULL — answer without hedging (FULL does not mean long)",
    "PARTIAL": (
        "PARTIAL — give the shape, name the specific-to-them part, bridge to the call"
    ),
    "SOFT_DEFLECT": (
        "SOFT DEFLECT — acknowledge it's fair, give the real reason it needs "
        "context, pivot"
    ),
    "HARD_DEFLECT": (
        "HARD DEFLECT — never confirm/deny/quantify; acknowledge, redirect to the "
        "counsellor"
    ),
}


def _pace_block(p: PacePlan) -> str:
    return (
        "\n## Pace & CTA (sales-playbook Part 2 — conversation clock)\n"
        f"chat_speed: {p.pace} — {p.pace_note}\n"
        f"message_depth: {p.message_depth} (substantive so far: "
        f"{p.substantive_depth} — real questions answered or qualifiers given, "
        "not small talk)\n"
        f"tone_stage: {p.tone_stage} — {p.tone_note}\n"
        f"cta_mode: {p.cta_mode} — {p.cta_note}\n"
        f"reply_length: {p.reply_length_hint}\n"
    )


def _topic_block(t: TopicMatch | None) -> str:
    if t is None:
        return (
            "\n## Topic handling\n"
            "No specific category matched. If it's a greeting / small talk / an "
            "acknowledgement / a vague opener, just answer it naturally and warmly "
            "— do not introduce cost, a country, fees, or booking that the lead "
            "did not raise. Otherwise use the triage philosophy: answer the "
            "cheap/legitimacy questions fully, give the shape then bridge on "
            "specific-to-them questions, hard-deflect anything touching the "
            "non-negotiables.\n"
        )
    lines = [
        "\n## Topic handling (combined topic matrix)\n",
        f"category: {t.rule.title}\n",
        f"handling: {_HANDLING_LABEL[t.rule.handling]}\n",
        f"how_much: {t.rule.how_much}\n",
    ]
    if t.hard_deflect_topics:
        names = "; ".join(f"{r.title} — {r.how_much}" for r in t.hard_deflect_topics)
        lines.append(
            f"ALSO in this message (HARD DEFLECT that part, answer the rest): {names}\n"
        )
    if t.high_intent:
        lines.append(
            "high_intent: yes — answer plainly and immediately ask for the call/"
            "office visit, regardless of message depth.\n"
        )
    return "".join(lines)


def _objection_block(o: Objection | None) -> str:
    if o is None:
        return ""
    return (
        "\n## Objection detected (sales-playbook Part 5)\n"
        f"type: {o.id}\n"
        f"handle_like_this: {o.script_hint}\n"
    )


def _country_gate_block(effective_country_discussed: bool) -> str:
    """Enforced state (director review), not a prompt hint: the guard actually
    blocks a draft that mentions the call / director's number / office while
    this is still false (see
    app/services/guard/guard.py:_check_premature_contact_offer). This note is
    just the heads-up so the model doesn't waste a regeneration finding that
    out — the real enforcement does not depend on this text being followed.
    ``effective_country_discussed`` already folds in the below-cutoff
    exemption, so this note never contradicts what the guard will allow.
    """

    if effective_country_discussed:
        return ""
    return (
        "\n## GATE — no call, number, or office yet (enforced, not optional)\n"
        "You have not yet had a real back-and-forth about a specific country "
        "with this lead — the guard will block any mention of a call, "
        "Rafique Sir's number, or the office until that happens, even if the "
        "Pace & CTA guidance above says a nudge is due. Focus this reply on "
        "actually discussing a country — ask which they're considering, or "
        "suggest one and see what they think. The CTA opens up once a "
        "country has genuinely been discussed both ways.\n"
    )


_STILL_DECIDING_CONTENT = (
    "Give this comparison (natural phrasing, but keep this substance):\n"
    "- Bangladesh — very close to India, but on the costlier side\n"
    "- Georgia — strong social/campus life\n"
    "- Russia — similar strengths to Georgia, slightly less on the social side\n"
    "- Uzbekistan, Kazakhstan, Kyrgyzstan — stable, well-established study "
    "environments, worth exploring\n"
    "End this message with a safety/facilities reassurance: separate facilities "
    "for male and female students, medical/health and security fully handled "
    "at every location, the student is the priority. Do NOT mention cost here "
    "unless they specifically ask.\n"
)

_RAFIQUE_INTRO_CONTENT = (
    "Introduce him using this framing (natural wording, keep this substance): "
    "\"our director, Rafique Sir, has 10+ years of experience in this field and "
    "can guide you better on the exact details — I'm just Stellar AI, an "
    "assistant. Please reach out to him directly for more specific guidance.\" "
    "Then give his number, per the contact rules elsewhere in this prompt.\n"
)


def _cycle_block(lead: Lead, effective_country_discussed: bool) -> str:
    """The qualification cycle — interest -> eligibility -> country -> Rafique
    Sir — as an ALWAYS-RETURN-TO spine, not a blocking sequence (director
    review). Exactly one step is "active" per turn: whichever is the next
    genuinely-unanswered one, driven entirely by tracked lead state (never
    message-depth alone), so a completed step is never re-asked and survives
    any number of side questions intact.

    This never tells the model to refuse or defer an unrelated real question —
    every branch says "after answering whatever they actually asked, weave in
    ...". The topic-matrix/triage system elsewhere already handles answering
    the question itself; this block only supplies what to steer back to.
    """

    # India-only: the abroad qualification cycle doesn't apply.
    if lead.considering_abroad is False:
        return ""

    # Step 1 — interest (answers the fixed opening message).
    if lead.considering_abroad is None:
        return (
            "\n## CYCLE STEP — interest (India vs abroad)\n"
            "You don't yet know whether this lead is exploring MBBS abroad, "
            "MBBS in India, or both — the fixed opening question. After "
            "answering whatever they actually asked, weave in ONE natural "
            "version of that question, once per reply, until they answer. "
            "Don't repeat it if they've already told you in any form.\n"
        )

    # Step 2 — eligibility: NEET score, then category/PCB (existing, tracked
    # persistently via lead.eligibility_flag — see app/services/eligibility.py).
    if lead.eligibility_flag is EligibilityFlag.UNKNOWN:
        return (
            "\n## CYCLE STEP — NEET score\n"
            "You don't know their NEET score yet — the next thing this cycle "
            "needs. After answering whatever they actually asked, weave in ONE "
            "natural ask for their NEET score, once per reply, until they "
            "answer. Don't ask again if a score is already on record.\n"
        )
    if lead.eligibility_flag is EligibilityFlag.NEEDS_CATEGORY:
        return (
            "\n## CYCLE STEP — reservation category (must resolve)\n"
            f"The lead's NEET score ({lead.neet_score}) sits in the band where "
            "whether the abroad route is open depends on their reservation "
            "category (general vs OBC/SC/ST/EWS) — this also decides which PCB "
            "percentage threshold applies. You do NOT know their category yet.\n"
            "- Do NOT tell them the route is open OR closed, and do NOT assume a "
            "category to reason from — not this turn, not later.\n"
            "- After answering whatever they actually asked, weave in ONE short, "
            "natural ask for their category (\"quick one — are you general "
            "category or OBC/SC/ST/EWS?\"). Ask it plainly, once per reply.\n"
            "- This stays open across every other topic (country, budget, "
            "safety, FMGE…) until they answer. Don't badger, but don't let it "
            "drop.\n"
        )
    if lead.eligibility_flag is EligibilityFlag.NEEDS_PCB:
        return (
            "\n## CYCLE STEP — PCB percentage (must resolve)\n"
            f"The lead's NEET score ({lead.neet_score}) clears the cutoff, but "
            "eligibility for the abroad route needs BOTH the NEET score AND "
            "their PCB (Physics+Chemistry+Biology) percentage — a good NEET "
            "score alone is not enough, and you do not know their PCB% yet.\n"
            "- Do NOT tell them they're eligible or confirm the route is open "
            "based on the NEET score alone — not this turn, not later.\n"
            "- After answering whatever they actually asked, weave in ONE short, "
            "natural ask for their PCB percentage (\"what was your PCB "
            "percentage — Physics, Chemistry, Biology combined?\"). Once per "
            "reply.\n"
            "- This stays open across every other topic until they answer. "
            "Don't badger, but don't let it drop.\n"
        )
    if lead.eligibility_flag is EligibilityFlag.BELOW_CUTOFF:
        # Diverted entirely — no country left to pick. The static prompt's
        # "NEET eligibility" section + the guard's below-cutoff exemption
        # already carry this the rest of the way (honest close, private-India
        # option, then the call).
        return ""

    # From here eligibility_flag is ABOVE_CUTOFF.

    # Step 3 — country decision: ask once, then never again.
    if not lead.target_country and not lead.country_still_deciding:
        return (
            "\n## CYCLE STEP — country decision\n"
            "Eligibility is confirmed. Next, find out whether they've already "
            "decided on a country or are still deciding between a few. After "
            "answering whatever they actually asked, weave in ONE natural ask "
            "about this, once per reply, until they answer.\n"
        )

    # Step 3b — deliver the content once (until a real back-and-forth has
    # happened — effective_country_discussed is that same tracked signal).
    if not effective_country_discussed:
        if lead.target_country:
            return (
                f"\n## CYCLE STEP — country: {lead.target_country} (decided)\n"
                f"They've settled on {lead.target_country}. Name it, then give "
                "the confirmed government college list for it from the "
                "knowledge snippets below (never invent a name beyond what's "
                "listed), then close with: \"We're also open to any specific "
                "college you have in mind — happy to look into that too.\"\n"
            )
        return "\n## CYCLE STEP — country: still deciding\n" + _STILL_DECIDING_CONTENT

    # Step 4 — Rafique Sir introduction (only reachable once
    # effective_country_discussed is true, i.e. the guard will actually allow
    # it) — exact first-time framing, once.
    if not lead.rafique_introduced:
        return (
            "\n## CYCLE STEP — introduce Rafique Sir (first time, use this framing)\n"
            + _RAFIQUE_INTRO_CONTENT
        )

    # Cycle complete. Hand off entirely to the normal pace/CTA system above —
    # it already re-surfaces Rafique Sir at natural, engagement-paced moments
    # (soft/direct CTA notes) without a separate mechanism here.
    return ""


def _deflection_block(plan: DeflectionPlan | None, settings: Settings) -> str:
    if plan is None:
        return ""
    return turn_hint(
        plan,
        counselor_name=settings.counselor_name.strip(),
        counselor_phone=settings.counselor_phone.strip(),
        office_address=settings.office_address.strip(),
    )
