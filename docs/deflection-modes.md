# Deflection Variety — 13 modes

Source: management file "Fallback & Deflection Variety" (round 3 of 4, 2026-09-10).

## Why

When the bot can't fully answer, it used to fall through to one fixed line
("let me set up a call with the counsellor"). Said once it's helpful; said three
times in a conversation it reads as a script and undoes the trust the rest of
the conversation builds. The fix is not a better sentence — it's **13 distinct
deflection modes, each triggered by a different reason for deflecting**, each
with its own register. The LLM writes fresh each turn within the chosen mode's
lane; the modes are never canned strings.

## Where it lives

| Piece | File |
|---|---|
| The 13 modes (data), selector, escalation + contact rules | `app/services/conversation/deflection.py` |
| Always-on compact reference in the system prompt | `deflection.modes_reference()` → `{{deflection_modes}}` in `app/prompts/system_prompt.md` |
| Per-turn: selected mode + full register + contact directive | `deflection.turn_hint()` → injected by `app/services/conversation/context.py` |
| Mode chosen from triage output + conversation state | `select_deflection()` called in `build_turn_context` |
| Safe-fallback (guard blocked twice): mode from the blocked rules, phrasing pool, no-repeat | `app/services/guard/fallback.py` |
| Chosen mode persisted per turn | `ConversationTrace.turn_signals["deflection"]` |

## The modes

1. Sensitive-topic (FMGE / licensing / "will the degree work in India")
2. Money-sensitive (financing, refunds, payment schedule, our fee amount)
3. Personalized-assessment ("which university for me", "my chances")
4. No-data-yet (a named university/country we have no confirmed detail on)
5. Out-of-scope (non-MBBS / unrelated services)
6. Repeat-ask (same question again after a first deflect — acknowledge the loop)
7. Pushy/frustrated lead
8. Legitimacy/trust ("are you real", "prove it") — **address**, not number
9. Emotional/anxious parent (safety, distance, child alone) — **address**
10. Comparison-shopping ("another agent said X")
11. Stall-detection ("send the full brochure/list")
12. Hard-blocked topic (premium-country figures, PG cost, guarantees) — **permanent**
13. Genuinely-unknown (no information at all — say so)

## Rules encoded

- **Mode is chosen by why we're deflecting**, not interchangeably.
- **Never the number and the address in the same message.** Default to the
  number (director's direct line); address only for modes 8 / 9 or when the lead
  asks about visiting / is local / raises legitimacy. Give the address once; if
  asked again, plainly and briefly.
- **Escalate, don't repeat.** First deflect: soft, no contact. Second: carries
  the number. Third — or a repeat of the same mode — becomes mode 6 (or mode 7
  if the lead is frustrated). `select_deflection` does this from `deflect_index`
  (counted from prior traces) and `last_mode`.
- **Never reuse the same sentence twice in one conversation.** The prompt states
  this; the guard fallback enforces it by skipping any pool line already in the
  recent bot text.
- **Mode 12 is permanent.** No data added later unlocks premium-country figures,
  PG cost, or admission guarantees — `select_deflection` never escalates or
  downgrades it.

## Related code fix (same round)

"What if my budget is 30 lakh" no longer falls through to a deflect. The guard's
`multi_country_cost` rule now recognises a **budget echo** — the bot confirming
the lead's *own* stated figure against a couple of countries, with no new range
span — and allows it, while still bounding the figure and still requiring the
director's number if Georgia/Nepal is named. See `app/services/guard/guard.py`
`_check_cost` and `GuardContext.lead_message`.
