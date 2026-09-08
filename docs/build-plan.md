# MBBS Abroad Lead Bot — Build Plan

> Source: plan authored by Aman, saved verbatim (light markdown cleanup only).
> Critique and open decisions: see [plan-critique.md](plan-critique.md).
> Behaviour layer: see [sales-playbook.md](sales-playbook.md),
> [topic-matrix.md](topic-matrix.md), [topic-matrix-2.md](topic-matrix-2.md).

## 1. What this system does

Converts ~1,200 existing NEET-appeared leads (students + parents) into booked
counseling calls/in-person meetings, via WhatsApp + an outsourced LLM, without
the bot ever trying to close the deal itself. The bot's only real job is:
build enough trust to get someone on a call with the human counselor.

Funnel: Lead → Contacted → Engaged → Qualified → Booked → (handed to counselor)

## 2. Non-negotiable behavior rules

These must be enforced regardless of what the LLM "wants" to say — treat them
as hard constraints checked outside the LLM's own judgment, not just
instructions inside its prompt.

- Never state exact costs for premium-tier countries (Germany/UK/US) — only
  "varies by country/package." Kazakhstan/Uzbekistan-tier (~30–35L) can be
  stated.
- Never confirm or deny a financing/loan option unless explicitly cleared for
  that specific case — default is to not mention it at all.
- Never guarantee admission, a specific intake, or a firm total cost.
- Never mention the PG (postgraduate) cost figure — internal only.
- Frame the NEET eligibility cutoff (213 general / 175 OBC, ~50%/45% PCB)
  narrowly and honestly: below this, government seats in India AND the abroad
  route are closed — private MBBS in India is still open if they can afford
  it. Never imply "you can't become a doctor at all."
- Adapt tone for student vs. parent based on the current message, never
  hard-lock an assumption for the whole thread — the same thread can switch
  speakers.
- Keep every reply short. Don't try to fully resolve a concern in chat —
  acknowledge it, then move toward booking.
- Hours 0–24 of engagement: push actively toward booking a call/meeting.
- Hours 24–48: shift tone — briefly acknowledge the specific concern, then
  honestly hand off to the human counselor as the one who can actually
  resolve it (bot admits its own limits rather than over-explaining).
- Past 48 hours with no booking: stop active pursuit, move to a slow,
  spaced-out nurture cadence instead.
- The RAG/knowledge-base content itself must already exclude confidential
  figures (premium-country costs, PG cost, financing specifics) before it's
  embedded — redacting after retrieval is too late.
- Anything not explicitly provided by Hamza (costs, guidelines, country data)
  must not be filled in from general knowledge — ask first.
- Never state specific payment schedules or refund/cancellation terms in chat.
  These are contractual — a bot stating them wrong creates real liability. Same
  severity tier as the financing and admission-guarantee rules above; enforced
  as a deterministic (Tier-1) Response Guard block, not prompt-only.
  (Rule 7, added 2026-09-08 — surfaced by topic-matrix-2 §9.)
- No sales/qualification conversation runs until the lead passes a conversational
  gate: (1) a plain opt-in ask, interpreted by the LLM/NLU layer (not string
  matching) as yes / no / unclear; (2) an age check → 18+ / under-18 / unclear.
  A clear no → respectful close + opt-out. A confirmed under-18 → stop, hold
  pending guidance (`is_minor=true`, `minor_policy_status=pending_review`), flag
  the counsellor — this is a SAFE PLACEHOLDER; the real minor process (parent
  outreach, data handling) is an open legal question, not finalised in code.
  An unclear answer is re-asked once, phrased differently; still unclear → parked
  for a human. Never treat an unclear answer as a yes.
  (Gate added 2026-09-08. Replaces the blanket refusal of all imported leads.)

## 3. Architecture

### Outreach side
- Existing Student DB (~1,200 leads): name, phone, NEET appeared, source,
  consent/opt-in
- Campaign/Outreach Engine: segmentation, consent checking, template
  selection, scheduling, rate limiting
- WhatsApp Cloud API (Meta, direct — no third-party BSP layer, which avoids
  the BSP's monthly fee + per-message markup, at the cost of self-managing
  template approval and WABA setup)

### Ingestion
- Meta webhook: signature verification, payload validation, idempotency,
  message persistence, event creation
- Redis: queue/lock — also the home for per-lead 24-hour window state
  (open/closed, expiry timestamp)

### Concurrency / race-condition handling (mandatory, all three layers)

WhatsApp bots hit this in production constantly — lead sends a follow-up
message before the first reply is ready, Meta retries webhook delivery, two
LLM calls fire in parallel and step on each other's state. All three of the
following must be implemented in the ingestion layer, in this order:

1. **Idempotency check (first, cheapest)** — Every Meta message ID is stored
   after processing. If the same ID arrives again (Meta webhook retry), skip
   entirely. Zero LLM call, zero duplicate reply.
2. **Debounce window (~500ms)** — After acknowledging a message to Meta, wait
   ~500ms before starting processing. If another message from the *same lead*
   arrives in that window, merge them into a single turn and process as one.
   This is what makes rapid-fire messages ("hi" + "I'm interested in Russia"
   sent 1s apart) feel like one thought rather than two separate replies.
3. **Per-lead lock (safety net)** — A Redis lock keyed to the lead's phone
   number is acquired before LLM processing begins, released after the
   outbound send completes. Any inbound message from the same lead while the
   lock is held waits its turn — never processes in parallel. Catches
   anything the debounce missed and prevents two parallel LLM calls reading
   stale state.

Each layer solves a different failure mode: idempotency kills Meta's
duplicate deliveries, debounce handles fast-typing users, lock handles
anything else. Skipping any one leaves a real production gap.

**Nice-to-have for later**: burst cancellation — if a lead machine-guns 5+
messages in a few seconds, cancel any in-progress LLM reply, wait for the
burst to end, then reply once to the whole cluster. More complex to
implement, worth adding after the three above are working.

### Conversation engine
- Orchestrator with four sub-modules: Context Builder, Intent Detection,
  Memory Manager, Stage Manager → decides Next Best Action (Answer / Ask
  Question / Educate / Handoff)
- RAG/KB (Countries, Universities, Eligibility, Fees, FAQs) + LLM (OpenAI)
  for understanding/reasoning/response
- Response Guard (Facts, Safety, Hallucination, Policy) sits between LLM
  output and WhatsApp send — this is where §2's hard rules get enforced in
  code, not left to the LLM's judgment

**Open question to resolve before building:** is "Stage" (from the
orchestrator) the same state as "LOW/NURTURE/HIGH" (from the parallel
lead-scoring pipeline below), or two separate state machines computed
independently? Pick one source of truth before Claude Code builds both.

### Parallel lead-scoring pipeline
- Student conversation → Signal Extraction → Student Profile → Lead/Intent
  Engine → LOW / NURTURE / HIGH
- HIGH → Counselling CTA → CALL or OFFICE → Counsellor CRM

### Deployment stack
- Cloudflare DNS in front of everything (TLS termination, hides origin IP)
- OCI (Oracle Cloud) Free Tier VPS running: Nginx (reverse proxy) → FastAPI
  (app server) → Redis + Celery (queue, window state, scheduled tasks) →
  PostgreSQL + pgvector (lead DB + vector store for the RAG KB — avoids
  paying for a separate vector DB service)
- Outbound to Meta WhatsApp Cloud API + OpenAI

This stack already has homes for the two previously-missing pieces: **Redis
for window/session state**, **Celery for the re-engagement scheduler**
(in-window nudges, window-reopen templates, spaced cold-retry rounds).

Worth deciding before Claude Code scaffolds this:
- **OCI free-tier shape**: Postgres + Redis + Celery workers + FastAPI all on
  one box needs more than the 1GB "micro" free shape comfortably allows — the
  Ampere A1 free tier (up to 4 OCPU / 24GB RAM) is a better fit if available.
- **Backups**: this box holds real personal data (names, phone numbers, NEET
  scores, likely from minors) on a single VPS — a scheduled Postgres backup
  is worth having before real leads touch it.
- **Cloudflare Tunnel** (free) is worth considering instead of exposing the
  OCI VPS's IP directly — optional, low-effort hardening.

## 4. Lead data model (draft fields)

- Contact: name, phone, city, language preference
- Eligibility: NEET score, category (general/OBC), pass/fail against cutoff
- Interest: target country (if stated), budget band (if stated), urgency/intake
  year
- Conversation state: current funnel stage, interest temperature (hot/mid/cold
  — re-assessed each turn, not fixed once), window open/closed + expiry
  timestamp, last inbound timestamp, last outbound timestamp
- Outcome: booking status, assigned counselor, no-show flag
- Flags: parent-involved-in-thread (soft signal, not locked)

## 5. Conversation state machine

```
Never contacted
    → [send template, marketing rate] → Contacted (awaiting reply)
Contacted
    → lead replies → Engaged (window open, free-form, LLM-driven)
    → no reply 24h → Silent — needs template to re-open
Engaged
    → 0–24h: active push toward booking
    → booked → handed to counselor (exit bot flow)
    → 24–48h no booking: acknowledge + hand-off-to-human framing
    → still no booking at 48h → Nurture (slow cadence, low priority)
    → window closes (24h no reply) → Silent — needs template to re-open
Silent
    → re-open template sent, replied → back to Engaged
    → no reply after N rounds (2–3, days apart) → Dormant (deprioritized)
```

## 6. Build phases (suggested order for Claude Code)

1. **BSP/Meta setup** — WhatsApp Cloud API access, INR billing check, submit
   initial templates for Meta approval early (approval takes time)
2. **Core pipe** — FastAPI app + Postgres schema + Meta webhook (signature
   verification, idempotency, message persistence). No LLM yet.
3. **LLM layer** — integrate OpenAI for in-window free-form replies; load the
   system prompt (behavior contract) — see §7
4. **Guardrail filter (Response Guard)** — hard-rule check on every outbound
   LLM message before send
5. **Window/state tracking + Celery scheduler** — track 24h windows in Redis,
   fire in-window nudges and window-reopen templates on schedule
6. **Cold-lead retry cadence** — spaced multi-day retry rounds for total
   non-responders, capped at 2–3 rounds
7. **Handoff + booking capture** — flag ready leads, notify counselor,
   capture booking outcome into Counsellor CRM
8. **RAG/KB + pgvector** — countries/universities/eligibility/fees content,
   pre-redacted per §2, embedded for retrieval
9. **Analytics dashboard** — funnel + cost tracking by message category

## 7. Still needed before/while building

- **System prompt text** (the bot's actual behavior contract) — top priority,
  not yet drafted
- Actual template copy + confirmed Meta category (marketing vs. utility) per
  template
- Premium-country (Germany/UK/US) package data — pending from Hamza
- Decision on whether to pursue Click-to-WhatsApp ads for the initial 1,200
  contact (free 72-hour window vs. paying marketing rate on all 1,200)
- Minor-data privacy check — a chunk of these leads are likely 17-year-olds;
  worth a legal look at data handling before launch
- Confirm final India per-message rate for service/utility messages once
  Meta publishes it (Oct 1, 2026 pricing change)
- OCI compute shape decision (see §3)

## 8. Suggested next step

Draft the system prompt (bot brain) — it's the one piece Claude Code can't
invent, since it's judgment (tone, escalation timing, exact hand-off
phrasing), not implementation. Once that's solid, this plan + the system
prompt give Claude Code everything it needs for a scoped first build task
(Phases 1–2), rather than a one-shot "build the whole system" prompt.
