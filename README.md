# MBBS Abroad Lead Bot — Phase 2

The ingestion pipe: FastAPI service + Postgres + Redis that receives WhatsApp
Cloud API webhooks, persists every message, runs one centralized lead state
machine, imports the ~1,200 legacy leads, and enforces opt-out / consent /
minor-policy rules on the outreach path. **No LLM yet** — that is Phase 3.

Planning docs: [docs/build-plan.md](docs/build-plan.md),
[docs/plan-critique.md](docs/plan-critique.md),
[docs/system-prompt.md](docs/system-prompt.md).

---

## What's implemented

| Area | Detail |
|---|---|
| **App** | FastAPI (`app/main.py`), structured JSON logging, request-id middleware, typed exception handlers |
| **DB** | PostgreSQL + SQLAlchemy 2.0 async, Alembic migration (`alembic/versions/0001_initial_schema.py`), portable to SQLite for tests |
| **Models** | `households`, `leads`, `consent_records`, `messages`, `webhook_events`, `lifecycle_transitions` — incl. age/`is_minor`, consent audit trail, `human_owned`, per-message cost fields, phone dedup |
| **State machine** | `app/services/state_machine.py` — one source of truth (plan §5). Pure `next_state()` + `apply_event()` with an audit row per transition. Funnel stage / engagement phase are *derived*, not stored. |
| **Webhook** | `GET` verification handshake; `POST` with `X-Hub-Signature-256` HMAC check, payload-hash dedup (idempotent on `processed`, so a failed event is retried), per-`wamid` dedup, advance-only out-of-order status reconciliation, placeholder creation when a status arrives before its message |
| **STOP / opt-out** | `app/services/stop_keywords.py` (multi-language, transliteration-aware) runs **before** any normal processing; sets a sticky `OPTED_OUT` state + consent record, closes the window |
| **Outreach guard** | `app/services/outreach.py` `evaluate()` — hard-blocks opted-out, unverified consent, unknown consent, minor-policy-not-cleared, human-owned, handoff-in-progress. `persist_outbound()` also hard-refuses opted-out leads. |
| **WhatsApp client** | `WhatsAppClient` ABC + `MetaWhatsAppClient` (real Graph API) + `FakeWhatsAppClient` (deterministic, default). Swap via `WHATSAPP_CLIENT`. |
| **Importer** | `leadbot import-leads` — E.164 normalization, invalid rows collected not fatal, merge-on-reimport (idempotent), DOB/age → minor policy, `parent_phone`/`family_id` → households |
| **CLI** | `leadbot check-config / import-leads / replay-webhook / show-lead / send-template` |
| **Tests** | 119 tests (`pytest`), unit + integration, real Alembic migration exercised in a subprocess |

### Unresolved Phase 1 items — encoded as explicit config, not guessed

Run `leadbot check-config` to see the live list. These are the blockers from
`docs/plan-critique.md`:

| Item | How the code represents it |
|---|---|
| Consent audit of legacy leads (A1) | `OUTREACH_REQUIRE_VERIFIED_CONSENT=true` → every imported lead has `consent_verified=false` → **all bot outreach to them is blocked** |
| DPDP / minor policy (A2) | `MINOR_DEFAULT_POLICY=pending_review` → detected minors are blocked from outreach |
| Which NEET year + cutoffs (B9) | `NEET_YEAR` / `NEET_CUTOFF_*` unset → `eligibility_flag` computes as `unknown`, never guessed |
| Kazakhstan/Uzbekistan stateable cost | `STATEABLE_COST_RANGE` unset → surfaced by `check-config`; not used by Phase 2 logic |

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

The `app` container runs `alembic upgrade head` on start (see
`docker/entrypoint.sh`), then serves on `localhost:8000`. It talks to `db:5432` /
`redis:6379` internally.

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
    whatsapp/             WhatsAppClient ABC + meta + fake + factory
  importer/csv_importer.py
  cli.py                  operator CLI
alembic/                  migration env + versions
tests/                    unit/ + integration/ + fixtures/
```
