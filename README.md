# MBBS Abroad Lead Bot — Phases 2–6

FastAPI service + Postgres + Redis + Celery that:

- **Phase 2** — receives WhatsApp Cloud API webhooks, persists every message, runs
  one centralized lead state machine, imports the ~1,200 legacy leads, enforces
  opt-out / consent / minor-policy rules.
- **Phase 3** — an LLM conversation engine that auto-replies to in-window
  messages: speaker detection, KB retrieval, a rendered system prompt, booking
  detection → counsellor handoff, and a full decision trace per turn.
- **Phase 4** — a deterministic **Response Guard** on every outbound reply
  (premium-cost figures, financing, payment/refund terms, admission guarantees, PG cost, length),
  with block → regenerate → safe-fallback.
- **Phase 5 + 6** — a Celery scheduler: window-expiry → `SILENT`, in-window
  nudges, time-based phase escalation, and spaced `SILENT`/`NURTURE` re-open
  template rounds → `DORMANT` at the cap.

Planning docs: [docs/build-plan.md](docs/build-plan.md),
[docs/plan-critique.md](docs/plan-critique.md),
[docs/system-prompt.md](docs/system-prompt.md).

---

## What's implemented

| Area | Detail |
|---|---|
| **App** | FastAPI (`app/main.py`), structured JSON logging, request-id middleware, typed exception handlers |
| **DB** | PostgreSQL + SQLAlchemy 2.0 async, Alembic migration (`alembic/versions/0001_initial_schema.py`), portable to SQLite for tests |
| **Models** | `households`, `leads`, `consent_records`, `messages`, `webhook_events`, `lifecycle_transitions` — incl. age/`is_minor`, consent audit trail, `human_owned`, per-message cost fields, phone dedup, in-chat qualifiers (`neet_score`/`neet_category`/`city`/`target_country`/`budget_band`/`urgency`/`parent_in_loop`) + derived `interest_temperature` + `lead_score` (LOW/NURTURE/HIGH) |
| **State machine** | `app/services/state_machine.py` — one source of truth (plan §5). Pure `next_state()` + `apply_event()` with an audit row per transition. Funnel stage / engagement phase are *derived*, not stored. |
| **Webhook** | `GET` verification handshake; `POST` with `X-Hub-Signature-256` HMAC check, payload-hash dedup (idempotent on `processed`, so a failed event is retried), per-`wamid` dedup, advance-only out-of-order status reconciliation, placeholder creation when a status arrives before its message |
| **Turn concurrency** (build-plan §3) | 1) idempotency — the two dedup layers above; 2) debounce — `celery` dispatch acks Meta immediately then a `process_lead_turn` task waits `TURN_DEBOUNCE_MS` and merges a lead's rapid-fire messages into one turn (`engine.handle_pending_turn`, unmerged messages get a `merged` trace); 3) per-lead Redis lock (`LeadTurnLock`, TTL-bounded) held across engine + send — a second queued turn retries until it frees. `inline` dispatch (default, dev/tests/`simulate`) runs the engine in-request, one message at a time. |
| **STOP / opt-out** | `app/services/stop_keywords.py` (multi-language, transliteration-aware) runs **before** any normal processing; sets a sticky `OPTED_OUT` state + consent record, closes the window |
| **Outreach guard** | `app/services/outreach.py` `evaluate()` — hard-blocks opted-out, unverified consent, unknown consent, minor-policy-not-cleared, human-owned, handoff-in-progress. `persist_outbound()` also hard-refuses opted-out leads. |
| **WhatsApp client** | `WhatsAppClient` ABC + `MetaWhatsAppClient` (real Graph API) + `FakeWhatsAppClient` (deterministic, default). Swap via `WHATSAPP_CLIENT`. |
| **Importer** | `leadbot import-leads` — E.164 normalization, invalid rows collected not fatal, merge-on-reimport (idempotent), DOB/age → minor policy, `parent_phone`/`family_id` → households |
| **Conversation engine** | `app/services/conversation/engine.py` — gates (autoreply on, bot-owned, window open, reply guard), speaker detection, **qualifier extraction** (`extraction.py`, playbook Part 6) → non-destructive lead update + eligibility recompute, KB retrieval, LLM draft, guard loop, auto-send, booking → HANDOFF, **lead scoring** (`scoring.py`, plan §3) → `interest_temperature` + `lead_score`, HIGH → one-time counsellor CTA. Extraction/scoring are best-effort and never lose a reply; engine failure never fails ingestion. |
| **LLM client** | `LLMClient` ABC + `AnthropicLLMClient` + `OpenAILLMClient` + `FakeLLMClient` (default). Pick via `LLM_PROVIDER`. Cheap classifier calls use a separate model. |
| **Response Guard** | `app/services/guard/` — deterministic Tier-1 detectors (money figures incl. word-forms, premium-country scope, approved stateable + India-comparison ranges, financing, payment/refund terms, guarantees, PG cost, meta-leak, length). `check()` is pure; the engine does block → regenerate (×`GUARD_REGENERATE_ATTEMPTS`) → `safe_fallback_message` + counsellor alert. |
| **Knowledge base** | `app/knowledge/kb.yaml` (hand-written, pre-redacted seed) + keyword retrieval. A redaction lint re-runs the guard detectors on load and refuses any chunk with a blocked figure (critique B3). pgvector is Phase 8. |
| **Decision trace** | `conversation_traces` — per inbound turn: speaker, phase, KB chunks, every draft + guard verdict, tokens, final action, booking, errors (critique C2). |
| **Handoff** | `handoff_notifications` + `app/services/handoff.py` — log + optional `COUNSELOR_WEBHOOK_URL` POST. Triggers: booking, phase-handoff (24–48h), guard-fallback, engine-error. |
| **Scheduler** | `app/scheduler/` — Celery app + beat, 4 sweeps every `SWEEP_INTERVAL_SECONDS` under a Redis lock: `expire_windows` (→ `SILENT`), `advance_engagement` (phase-handoff alert / → `NURTURE` at 48h), `send_in_window_nudges` (canned nudge in the push phase), `run_reengagement` (`SILENT`/`NURTURE` re-open template rounds → `DORMANT`). All outbound goes through `OutreachService`; re-engagement templates need `purpose="outreach"` so the consent gate applies. |
| **CLI** | `leadbot check-config / import-leads / replay-webhook / show-lead / send-template / simulate / run-sweeps` |
| **Tests** | 237 tests (`pytest`) — unit + integration, incl. an adversarial guard suite (critique C1), the scheduler sweeps, and real Alembic migrations in a subprocess |

### Unresolved Phase 1 items — encoded as explicit config, not guessed

Run `leadbot check-config` to see the live list. These are the blockers from
`docs/plan-critique.md`:

| Item | How the code represents it |
|---|---|
| Consent audit of legacy leads (A1) | `OUTREACH_REQUIRE_VERIFIED_CONSENT=true` → every imported lead has `consent_verified=false` → **all bot outreach to them is blocked** |
| DPDP / minor policy (A2) | `MINOR_DEFAULT_POLICY=pending_review` → detected minors are blocked from outreach |
| Company / counsellor name (§7) | `COMPANY_NAME` / `COUNSELOR_NAME` unset → the system prompt renders "our team" / "our counsellor", and identity/credential questions stay generic (nothing invented) |

**Resolved 2026-09-08 (Hamza):** NEET year + cutoffs (`NEET_YEAR=2026`,
`NEET_CUTOFF_GENERAL=213`, `NEET_CUTOFF_OBC=175`); stateable cost tier
(`STATEABLE_COST_RANGE=₹30–35 lakh` for Kazakhstan + Uzbekistan only —
Kyrgyzstan stays unstated); India-vs-abroad comparison figure
(`INDIA_COMPARE_COST_RANGE=₹80L–1.2Cr`). Build-plan §2 **rule 7** added:
payment schedules and refund/cancellation terms are a deterministic guard block
(`payment_terms_disclosure`).

---

## Prerequisites

- Python 3.11+ (developed on 3.13)
- Docker Desktop (for Postgres + Redis) — or a native Postgres 14+/Redis 7

---

## Setup

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"     # Windows
# source .venv/bin/activate && pip install -e ".[dev]"  # macOS/Linux

cp .env.example .env
```

Start Postgres + Redis (published on host ports **5433 / 6380** to avoid clashing
with any native Postgres/Redis on the defaults):

```bash
docker compose up -d db redis
```

`.env` defaults already point at `localhost:5433` / `localhost:6380`.

---

## Migrate the database

```bash
.venv/Scripts/python.exe -m alembic upgrade head
```

Downgrade / re-check:

```bash
.venv/Scripts/python.exe -m alembic downgrade base
.venv/Scripts/python.exe -m alembic upgrade head
```

---

## Import the leads

CSV columns are matched by alias (case-insensitive) — see
`app/importer/csv_importer.py` `_COLUMN_ALIASES`. `phone` (any alias) is the only
required column. `parent_phone` creates a linked parent lead; `family_id` groups
a household.

```bash
.venv/Scripts/python.exe -m app.cli import-leads path/to/leads.csv --source legacy_db
.venv/Scripts/python.exe -m app.cli import-leads path/to/leads.csv --dry-run   # parse only, roll back
```

Try it with the sample file:

```bash
.venv/Scripts/python.exe -m app.cli import-leads tests/fixtures/leads_sample.csv --source legacy_db
```

Re-running is safe: existing leads are updated (missing fields filled), never
duplicated.

---

## Run the API

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

| Endpoint | Purpose |
|---|---|
| `GET /` | service info |
| `GET /healthz` | liveness |
| `GET /readyz` | readiness — checks DB + Redis (503 if either down) |
| `GET /webhook/whatsapp` | Meta verification handshake |
| `POST /webhook/whatsapp` | Meta event ingestion (HMAC-verified) |

```bash
curl -s localhost:8000/readyz
curl -s "localhost:8000/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=dev-verify-token&hub.challenge=ping"
```

---

## Replay webhook payloads

`replay-webhook` signs the body with `META_APP_SECRET` and POSTs it, exactly as
Meta would.

```bash
.venv/Scripts/python.exe -m app.cli replay-webhook tests/fixtures/webhook_inbound_text.json
.venv/Scripts/python.exe -m app.cli replay-webhook tests/fixtures/webhook_inbound_text.json   # again -> "duplicate"
.venv/Scripts/python.exe -m app.cli replay-webhook tests/fixtures/webhook_inbound_stop.json   # -> lead OPTED_OUT
.venv/Scripts/python.exe -m app.cli replay-webhook tests/fixtures/webhook_status_delivered.json
```

Inspect a lead:

```bash
.venv/Scripts/python.exe -m app.cli show-lead +919812345670
```

```
lead ...  +919812345670  (Priya Sharma)
  state       : engaged  (funnel: engaged, phase: push)
  consent     : opted_in  verified=False
  minor       : no  policy=not_applicable
  window open : True  expires=2026-09-08 09:57:08
  messages    : 1
    [inbound ] text      received  Hi, I want to know about MBBS in Georgia...
  transitions : 1
    never_contacted -> engaged  on inbound_message  (lead)
```

### Test STOP handling directly

```bash
# after replaying an inbound from 919812345670, send a STOP:
printf '%s' '{"object":"whatsapp_business_account","entry":[{"id":"W","changes":[{"field":"messages","value":{"messaging_product":"whatsapp","metadata":{"display_phone_number":"919000000000"},"messages":[{"from":"919812345670","id":"wamid.STOP99","timestamp":"1725700000","type":"text","text":{"body":"please stop"}}]}}]}]}' > stop.json
.venv/Scripts/python.exe -m app.cli replay-webhook stop.json
.venv/Scripts/python.exe -m app.cli show-lead +919812345670   # -> state: opted_out
```

---

## Conversation engine (Phases 3–4)

When `LLM_PROVIDER`, a knowledge base, and a WhatsApp client are all wired (they
are, by default with `fake`), a replayed inbound message triggers an auto-reply.

With `WEBHOOK_CONVERSATION_DISPATCH=inline` (the code default, used by tests and
`simulate`) the reply is produced inside the webhook request. With `=celery` (the
`.env.example` / docker default) the webhook only enqueues `process_lead_turn`;
the **worker must be running** for a reply to be sent, and the turn goes through
the debounce + per-lead lock described above.

See it without a webhook:

```bash
.venv/Scripts/python.exe -m app.cli simulate +919812345670 "my son wants MBBS in Georgia, is it safe?"
```

```
action     : sent
bot reply  : Thanks for reaching out! ... Would a quick call with our counsellor tomorrow work?
booking    : False
trace      : attempts=1 verdict=allowed kb=['country-georgia', 'overview', ...] tokens=1366/23
```

`simulate` with the `fake` provider always returns the same canned reply. To see
the **Response Guard** block a bad reply, run the adversarial tests
(`tests/integration/test_guard_adversarial.py`) or point at a real provider:

```bash
# .env
LLM_PROVIDER=anthropic          # or: openai
ANTHROPIC_API_KEY=sk-ant-...    # or: OPENAI_API_KEY=sk-...
COMPANY_NAME=YourCo
COUNSELOR_NAME=Dr. Rao
```

The guard runs identically for every provider. Blocked-then-unfixable replies
send a canned fallback and queue a `handoff_notifications` row (delivered to
`COUNSELOR_WEBHOOK_URL` if set, else logged). Every turn writes a
`conversation_traces` row — inspect with `leadbot show-lead` or query the table.

Mute the bot entirely with `BOT_AUTOREPLY_ENABLED=false` (ingestion still runs).

---

## Scheduler (Phases 5–6)

The Celery scheduler drives everything time-based: closing 24h windows, nudging
quiet leads, escalating to a human at 24–48h, and the spaced re-open rounds for
`SILENT` / `NURTURE` leads. Run all four sweeps once, by hand:

```bash
.venv/Scripts/python.exe -m app.cli run-sweeps
```

```
{"status": "ok", "sweep": "expire_windows", "scanned": 1, "acted": 1, ...}
{"status": "ok", "sweep": "advance_engagement", "scanned": 1, "acted": 1, ...}
{"status": "ok", "sweep": "send_in_window_nudges", "scanned": 0, ...}
{"status": "ok", "sweep": "run_reengagement", "scanned": 1, "acted": 1, ...}
```

On a schedule, run a worker + beat (both need the same env + DB/Redis):

```bash
.venv/Scripts/python.exe -m celery -A app.scheduler.celery_app worker --loglevel=info
.venv/Scripts/python.exe -m celery -A app.scheduler.celery_app beat --loglevel=info
```

`docker compose up` starts `worker` and `beat` alongside `app`. Beat fires each
sweep every `SWEEP_INTERVAL_SECONDS` (default 300); a Redis lock stops overlapping
ticks from double-processing.

**Templates:** `run_reengagement` sends `REENGAGE_TEMPLATE_NAME` / `NURTURE_TEMPLATE_NAME`
— those must exist and be Meta-approved for real sends (plan §7, still pending).
With `WHATSAPP_CLIENT=fake` they "send" fine. Re-engagement uses
`purpose="outreach"`, so with `OUTREACH_REQUIRE_VERIFIED_CONSENT=true` every
imported lead is skipped (logged, `next_reengagement_at` pushed out) until the
Phase 1 consent audit.

---

## Tests

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check app tests
```

Tests run against SQLite + `fakeredis` — **no Docker needed for `pytest`**.
`tests/integration/test_migrations.py` shells out to real Alembic to prove the
migration applies and matches the models.

---

## Run everything in Docker

```bash
docker compose up --build
```

`app` runs `alembic upgrade head` on start (see `docker/entrypoint.sh`), then
serves on `localhost:8000`. `worker` and `beat` run the Celery scheduler. All
talk to `db:5432` / `redis:6379` internally.

---

## Point at a real WhatsApp number

1. Create a Meta app + WhatsApp product; get the **test number** (free, up to 5
   recipients) or connect a real number.
2. In `.env`:
   ```
   WHATSAPP_CLIENT=meta
   META_APP_SECRET=<from App Dashboard > Settings > Basic>
   META_VERIFY_TOKEN=<any string you choose>
   META_ACCESS_TOKEN=<System user / temp token>
   META_PHONE_NUMBER_ID=<from WhatsApp > API Setup>
   ```
3. Configure the webhook callback URL to `https://<your-host>/webhook/whatsapp`
   with the same verify token. Subscribe to the `messages` field.
4. Nothing else changes — the `WhatsAppClient` abstraction and webhook processor
   are identical for fake and real.

---

## Project layout

```
app/
  main.py                 FastAPI app factory + lifespan
  config.py               pydantic-settings; unresolved_phase1_items
  api/                    routes (health, webhook), deps, middleware, error handlers
  db/                     async engine/session, declarative base
  models/                 SQLAlchemy models + enums
  schemas/webhook.py      Pydantic models for the Meta payload
  security/signature.py   X-Hub-Signature-256 sign + verify
  services/
    phone.py              E.164 normalization
    state_machine.py      the single lifecycle state machine
    stop_keywords.py      opt-out detection
    consent.py            consent records, minor evaluation, opt-out
    windows.py            24h service window (Redis + DB mirror)
    messages.py           inbound/outbound persistence, status reconciliation
    webhook_processor.py  the core ingestion orchestration
    outreach.py           the outreach guard + send methods
    eligibility.py        NEET cutoff -> flag (or unknown)
    pricing.py            optional rate-card cost estimation
    handoff.py            counsellor notifications (log / webhook)
    nudges.py             canned in-window nudge copy
    whatsapp/             WhatsAppClient ABC + meta + fake + factory
    llm/                  LLMClient ABC + anthropic + openai + fake + factory
    knowledge/            KnowledgeBase ABC + YAML KB + redaction lint
    guard/                Response Guard — detectors, guard, safe fallback
    conversation/         prompt render, speaker, context, booking, engine
  scheduler/             Celery app + beat, sweeps, locks, sync/async runner
  knowledge/kb.yaml       hand-written pre-redacted seed KB
  prompts/system_prompt.md  operational system prompt (rendered with config)
  importer/csv_importer.py
  cli.py                  operator CLI
alembic/                  migration env + versions (0001–0003)
tests/                    unit/ + integration/ + fixtures/
```
