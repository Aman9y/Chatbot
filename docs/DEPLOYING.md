# Deploying for real (OCI + Cloudflare)

Target stack (build-plan §3): **Cloudflare (DNS / TLS / tunnel) → Nginx →
FastAPI → Redis + Celery → PostgreSQL + pgvector**, outbound to the Meta
WhatsApp Cloud API and the LLM provider.

The application code is deploy-ready. Everything below is infra, external
accounts, and operational glue that is **not in the repo yet**.

---

## 0. Prerequisites / decisions still open

These block a real launch and are not code:

- **DPDP / minors legal review.** The age gate is a safe placeholder; the actual
  parent-consent + data-retention process is unresolved. `MINOR_DEFAULT_POLICY`
  stays `pending_review` until then.
- **Legacy-lead consent audit (critique A1).** The in-chat opt-in gate is the
  path through `OUTREACH_REQUIRE_VERIFIED_CONSENT`; you turn on
  `CONSENT_ASK_SWEEP_ENABLED` only after (a) the `gate_consent` template is Meta-
  approved and (b) you've decided to start contacting the ~1,200.
- **`COMPANY_NAME` / `COUNSELOR_NAME` / `COUNSELOR_PHONE` / `OFFICE_ADDRESS`.**
  Set as of 2026-09-09 (Stellar Educonsultancy / Rafique Shaikh / +91 74478
  67887 / Mira Road East, Thane). `MAPS_LINK` is still unset — office-visit
  replies share the address without a map link until it is provided.
- **`COUNTRY_COST_RANGES` / `SENSITIVE_COST_COUNTRIES`.** Seven approved
  per-country ranges are configured; Georgia and Nepal are gated (reason for the
  higher band + `COUNSELOR_PHONE` required in the same reply). Any country not in
  the JSON gets no figure.
- **Premium-country (Germany/UK/US) package data.** Guard blocks all figures for
  those until provided.
- **Paid LLM key.** Free-tier Gemini 503s under load. Same `GEMINI_API_KEY` var.
- **OpenRouter data handling (`LLM_PROVIDER=openrouter`).** This provider sends
  every lead message + the profile-bearing system prompt + recent history
  through OpenRouter's servers to an upstream host — a **new sub-processor**.
  Handled (2026-09-10): a per-request routing pin restricts `google/gemini-3.7-flash`
  to **Google Vertex only** (`provider.only=google-vertex`, `data_collection=deny`,
  `zdr`, no fallback), proven with live calls; `OPENROUTER_DATA_POLICY_CONFIRMED=true`.
  See `docs/llm-data-handling.md`. **Still open on the legal track:** name
  OpenRouter as a sub-processor in the DPDP / records-of-processing work; set the
  OpenRouter account to disallow training providers as defence-in-depth.
- **WABA messaging tier.** A new WABA starts at 250–1,000 business-initiated
  conversations / 24h and only ramps as quality stays green. Plan the opt-in
  drip (`CONSENT_ASKS_PER_SWEEP`) around that, not a blast.

---

## 1. Meta / WhatsApp  (do first — approvals take days)

1. Meta app + **WhatsApp** product. Get a **WABA** and a **phone number** (test
   number is free for ≤5 recipients; a real number needs business verification).
2. Confirm **INR billing** on the WABA.
3. **Submit templates for approval now** (names must match `.env`):
   - `gate_consent_v1` — the opt-in ask
   - `reengage_v1` — SILENT re-open
   - `nurture_v1` — NURTURE cadence
   Pick the Meta category (marketing vs utility) per template — it changes the
   per-message price.
4. **App Dashboard → Settings → Basic** → App Secret → `META_APP_SECRET`.
5. **WhatsApp → API Setup** → Phone number ID → `META_PHONE_NUMBER_ID`; generate
   a **System User token** → `META_ACCESS_TOKEN`.
6. Choose any string for `META_VERIFY_TOKEN`.
7. Webhook config comes after you have a public URL (§5).

---

## 2. The OCI box

1. Launch **VM.Standard.A1.Flex** (Ampere ARM free tier, up to 4 OCPU / 24 GB).
   The 1 GB "micro" shape is too small for Postgres + Redis + Celery + FastAPI on
   one host. Ubuntu 22.04 / 24.04.
2. Security list / NSG: inbound **443** (and 80 for ACME) from anywhere, **22**
   from your IP only. Do **not** open 8000 / 5432 / 6379.
3. Install either Docker + the compose plugin, or native
   `postgresql-16` + `postgresql-16-pgvector`, `redis-server`, `python3.12`,
   `nginx`.
4. The repo Dockerfile is `linux/amd64`. On ARM, build on the box
   (`docker compose build`) or add `platform: linux/arm64`. Native install
   sidesteps this.

---

## 3. Datastores

- **Postgres**: create the `chatbot` role + DB; `CREATE EXTENSION IF NOT EXISTS
  vector;` (pgvector isn't used until the Phase 8 RAG KB, enable it anyway).
  Listen on `localhost` only.
- **`DATABASE_URL` scheme**: the app needs an async driver, but you can hand it
  the plain URL a PaaS injects. `config.py` normalises `postgres://`,
  `postgresql://`, `postgresql+psycopg2://` and `postgresql+psycopg://` to
  `postgresql+asyncpg://` automatically, strips libpq-only query params
  (`sslmode`, `channel_binding`, …) and maps `sslmode=require` → `?ssl=true`.
  Setting the URL already in `postgresql+asyncpg://…` form also works.
- **Redis**: bind `127.0.0.1`, set `requirepass`, `appendonly yes`. Put the
  password in `REDIS_URL` / `CELERY_BROKER_URL`.
- Migrate once: `python -m alembic upgrade head` (or let the Docker `app`
  entrypoint do it — it runs `alembic upgrade head` on boot).

---

## 4. App + worker + beat as managed services

Production `.env` on the box: root-owned, `chmod 600`, **not in git**. Real
`META_*`, a **paid** `GEMINI_API_KEY`, `APP_ENV=production`,
`WHATSAPP_CLIENT=meta`, `WEBHOOK_CONVERSATION_DISPATCH=celery`,
`CONSENT_ASK_SWEEP_ENABLED=false` (until §0 is cleared).

**Docker route:** a `docker-compose.prod.yml` override that (a) removes the
`ports:` publish for `db`/`redis`, (b) publishes `app` only on
`127.0.0.1:8000`, (c) `env_file: /etc/leadbot/.env`. Then
`docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`.

**Native route:** three `systemd` units (write these — none exist yet):

| unit | command |
|---|---|
| `leadbot-api` | `uvicorn app.main:app --host 127.0.0.1 --port 8000` (for real traffic consider `gunicorn -k uvicorn.workers.UvicornWorker -w 2` — `gunicorn` would need adding to `pyproject.toml`; a single uvicorn is fine at ~1,200-lead volume) |
| `leadbot-worker` | `celery -A app.scheduler.celery_app worker --loglevel=info --concurrency=2` (prefork is fine on Linux) |
| `leadbot-beat` | `celery -A app.scheduler.celery_app beat --loglevel=info --schedule /var/lib/leadbot/celerybeat-schedule` |

All: `EnvironmentFile=/etc/leadbot/.env`, `Restart=always`, run as a non-root
`leadbot` user.

---

## 5. Nginx + Cloudflare

1. **Nginx** (`/etc/nginx/sites-available/leadbot` — write it): `server` on 443,
   `proxy_pass http://127.0.0.1:8000;`, forward `X-Forwarded-For` / `Host`,
   `client_max_body_size 1m`, `proxy_read_timeout 60s` (LLM turns take ~10s).
   Only `GET|POST /webhook/whatsapp` needs to be public.
2. **Cloudflare**: add the domain, `A` record → OCI public IP (proxied). Then
   either:
   - **Cloudflare Tunnel** (`cloudflared`) — the plan's recommendation: no
     inbound ports open at all; the tunnel connects out from the box. Nginx
     optional.
   - or Cloudflare Origin Certificate + Nginx TLS + "Full (strict)" mode.
3. Origin TLS: Cloudflare Origin cert (15-year) or Let's Encrypt via `certbot`.

---

## 6. Point Meta at it

Meta → WhatsApp → Configuration → Webhook:
- Callback URL: `https://<domain>/webhook/whatsapp`
- Verify token: your `META_VERIFY_TOKEN`
- **Subscribe to the `messages` field.**

Meta GETs the URL and expects `hub.challenge` echoed back (the handshake in the
run guide). Then set `WHATSAPP_CLIENT=meta` and restart the app.

---

## 7. Operational must-haves before real leads (critique C3)

- **Postgres backups** — cron `pg_dump` → **OCI Object Storage** (off-box).
  Mandatory before real PII touches the box (names, phone numbers, NEET scores,
  likely from minors).
- **Uptime monitor** — external service hitting `https://<domain>/readyz` every
  1–5 min, alerting to a phone. A 12h outage silently closes 24h conversation
  windows.
- **Restore runbook** — write it, test it. OCI has reclaimed idle free-tier
  instances before.
- **Log capture** — the app logs structured JSON to stdout (masked phone
  numbers, never keys). Ship it somewhere queryable.

---

## 8. First-run sequence on the box

```
1. datastores up + migrated
2. leadbot check-config          # resolve or accept every item
3. api + worker + beat up        # /readyz -> ready, worker "ready.", beat firing
4. Meta webhook configured + verified
5. import the leads:  leadbot import-leads leads.csv --source legacy_db
   (every lead imports with consent_gate=pending_opt_in, outreach blocked)
6. leave CONSENT_ASK_SWEEP_ENABLED=false until §0 is cleared and templates are live
7. when ready: flip the sweep on -> the opt-in drip begins at CONSENT_ASKS_PER_SWEEP/tick
```
