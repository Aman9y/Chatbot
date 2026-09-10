# Running the bot locally

Two paths. **A** needs nothing installed (portable Redis + SQLite, one command).
**B** is the full Postgres + Celery stack via Docker, closer to production.

All commands run from the repo root in **Git Bash**. `PY` below =
`.venv/Scripts/python.exe` (the project venv, Python 3.12).

---

## A. One-command local run (no Docker)

What it uses: a portable Redis in `.local/redis/`, a SQLite file `leadbot.db`,
and the FastAPI app. Replies are generated in-request
(`WEBHOOK_CONVERSATION_DISPATCH=inline`) — no Celery worker or beat.

### One-time setup

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
cp .env.example .env
```

Edit `.env`:

| key | value | why |
|---|---|---|
| `WEBHOOK_CONVERSATION_DISPATCH` | `inline` | replies in-request, no Celery |
| `DATABASE_URL` | `sqlite+aiosqlite:///./leadbot.db` | no Postgres server |
| `REDIS_URL` | `redis://localhost:6379/0` | the portable Redis |
| `LLM_PROVIDER` | `fake`, `gemini`, or `openrouter` | `fake` = canned replies, offline; others = real |
| `GEMINI_API_KEY` | *(your key)* | only if `LLM_PROVIDER=gemini` |
| `GEMINI_MODEL` | `gemini-3.5-flash` | free-tier keys 503 on the flagship models |
| `OPENROUTER_API_KEY` | *(your key)* | only if `LLM_PROVIDER=openrouter` (route `google/gemini-3.7-flash`) |
| `OPENROUTER_DATA_POLICY_CONFIRMED` | `false` | set `true` only after reading `docs/llm-data-handling.md` — this provider adds OpenRouter as a data processor |

The portable Redis is fetched automatically the first time you run the script
(needs internet once). If you're offline, drop any `redis-server` /
`redis-server.exe` on your PATH instead.

### Run

```bash
bash scripts/run-local.sh
```

That starts Redis, applies migrations, prints a config recap, and runs the app
in the foreground. **Ctrl+C stops the app and the Redis it started.**

### Confirm it works (second terminal)

```bash
curl -s localhost:8000/healthz          # -> {"status":"ok"}          process up
curl -s localhost:8000/readyz           # -> {"status":"ready", ...}   DB + Redis reachable
curl -s "localhost:8000/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=dev-verify-token&hub.challenge=ping"
                                        # -> ping                      Meta handshake
```

`/readyz` returning `ready` with `database: ok` and `redis: ok` is the real
check.

### Drive a conversation

```bash
# one turn straight into the engine (skips the consent gate):
.venv/Scripts/python.exe -m app.cli simulate +919812345670 "is Georgia safe for MBBS?"

# or a real signed webhook (full ingestion -> gate -> engine -> reply):
.venv/Scripts/python.exe -m app.cli replay-webhook tests/fixtures/webhook_inbound_text.json
.venv/Scripts/python.exe -m app.cli show-lead +919812345670   # see state, gate, messages, trace
```

A fresh lead lands in the **consent + age gate** (`gate: pending_age`) and gets
the opt-in / "are you 18+?" questions before any sales reply. Answer with an
adult age and the sales flow (and the LLM) kick in.

### Stop / reset

```bash
bash scripts/stop-local.sh    # kill app + portable Redis
rm -f leadbot.db              # wipe all local data
```

---

## B. Full stack: Postgres + Redis + Celery (Docker)

Closer to production: real Postgres, and the webhook acks Meta immediately then a
Celery worker processes each turn (debounce + per-lead lock).

### Setup

1. Install **Docker Desktop**, launch it, wait for the whale icon to settle.
   `docker --version` and `docker compose version` should both work.
2. `.env`: set `WEBHOOK_CONVERSATION_DISPATCH=celery`,
   `DATABASE_URL=postgresql+asyncpg://chatbot:chatbot@localhost:5433/chatbot`,
   `REDIS_URL=redis://localhost:6380/0` (the compose file publishes Postgres on
   host **5433** and Redis on **6380** to dodge native installs).

### Start — datastores in Docker, app processes native

```bash
docker compose up -d db redis
docker compose ps                                   # both -> running (healthy)
docker compose exec db pg_isready -U chatbot        # -> accepting connections
docker compose exec redis redis-cli ping            # -> PONG

.venv/Scripts/python.exe -m alembic upgrade head    # apply schema
```

Then **three terminals**, left running:

```bash
# terminal 1 — API
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000

# terminal 2 — Celery worker  (Windows needs --pool=solo)
.venv/Scripts/python.exe -m celery -A app.scheduler.celery_app worker --pool=solo --loglevel=info

# terminal 3 — Celery beat (fires the 5 sweeps on SWEEP_INTERVAL_SECONDS)
.venv/Scripts/python.exe -m celery -A app.scheduler.celery_app beat --loglevel=info
```

Confirm:

| process | healthy when the log shows | also check |
|---|---|---|
| API | `Application startup complete` | `curl localhost:8000/readyz` -> `ready` |
| worker | `celery@<host> ready.` + a `[tasks]` list of 6 | not looping on `Cannot connect to redis` |
| beat | `Scheduler: Sending due task ...` within `SWEEP_INTERVAL_SECONDS` | matching `Task ... succeeded` in the worker log |

Force one sweep cycle without waiting:

```bash
.venv/Scripts/python.exe -m app.cli run-sweeps   # -> a JSON line per sweep
```

Now `replay-webhook` a fixture and watch the **worker** terminal pick up
`process_lead_turn`.

### Everything in Docker (alternative to the three terminals)

```bash
docker compose up --build          # db, redis, app (migrates on boot), worker, beat
docker compose logs -f app worker beat
```

### Stop

```bash
docker compose down        # keep data
docker compose down -v     # wipe Postgres + Redis volumes
```

---

## Notes

- `leadbot check-config` prints the resolved config and the outstanding
  Phase-1 items (company/counsellor name, DPDP policy, consent-ask sweep). Those
  are expected locally.
- `WHATSAPP_CLIENT=fake` means outbound "sends" are logged, not delivered. To hit
  a real Meta test number see the README ("Point at a real WhatsApp number").
- Free-tier Gemini returns `503 "high demand"` intermittently under the bot's
  ~3.5k-token prompt — just retry, or use a paid key.
- The Celery beat writes `celerybeat-schedule*` files in the repo root (ignored).
