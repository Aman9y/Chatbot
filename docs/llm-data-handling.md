# LLM data handling — where lead conversation data goes

Switching `LLM_PROVIDER` to `openrouter` (route `google/gemini-3.7-flash`) is a
**data-handling change, not a routing detail**. This file records the data path
and the provider policy that must be confirmed before real leads are messaged.

## What is sent, and to whom

Every turn the conversation engine sends to the model:

- the lead's inbound message(s) for that turn;
- the assembled system prompt, which embeds the lead's **extracted profile**
  (name if given, city, NEET score + category, target country, budget band,
  intake year, whether a parent is involved);
- the recent conversation history (up to `CONVERSATION_HISTORY_TURNS`).

| Provider setting | Processors in the path |
|---|---|
| `gemini` (previous) | **Google** only (Gemini Developer API, key-scoped) |
| `openrouter` (now) | **OpenRouter** → then the upstream host it routes to (**Google** for the `google/*` route) |

So `openrouter` adds OpenRouter as a **second external processor** that sees the
full lead conversation content. Under DPDP / the consent question in
`docs/DEPLOYING.md` this is a new sub-processor and the records-of-processing /
any DPA must reflect it.

## OpenRouter's stated policy (as read 2026-09-10 — re-verify before launch)

From OpenRouter's privacy policy and privacy/logging docs:

- **Prompt/response content logging is OFF by default.** OpenRouter does not
  retain prompt or completion **content** unless the account opts into prompt
  logging (which it offers a 1% usage discount for, and which grants OpenRouter
  broad rights to that data — **do not enable it**).
  Sources: <https://openrouter.ai/docs/features/privacy-and-logging>,
  <https://openrouter.ai/privacy>
- **Request metadata is always retained** — timestamps, model, token counts,
  latency — for billing/ops. No retention period is published.
- **OpenRouter states it does not use Inputs or Outputs for model training.**
  Individual **model providers may**. OpenRouter exposes:
  - an account setting for whether to *allow routing to providers that may train
    on your data* (set this to **disallow**);
  - a per-request **data-policy filter** / the `zdr` (zero-data-retention)
    parameter to force no logging regardless of account settings.
- **Upstream (Google) has its own policy.** For a `google/*` model this depends
  on whether OpenRouter routes it via **Google AI Studio** (Gemini Developer
  API — historically may use data to improve Google products on the free/standard
  tier) or **Google Vertex AI** (does not train on customer data; configurable
  retention). OpenRouter shows the retention + training row for each provider
  endpoint in its model/provider table — **this specific row for
  `google/gemini-3.7-flash` must be checked and recorded**, not assumed.
- Regional (EU/US) processing endpoints exist for enterprise accounts only.

## Before this points at real leads — checklist

1. Open OpenRouter's provider table for `google/gemini-3.7-flash`; record the
   data-retention period and the "trains on data" flag for the endpoint(s) it
   can route to. Screenshot it into this file's history.
2. In the OpenRouter account: set "providers that may train on your data" to
   **disallow** (both paid and free toggles).
3. Decide whether to also send `zdr` per request (belt-and-braces; costs
   nothing, may reduce provider availability). If yes, wire it in the client.
4. Confirm the upstream Google endpoint is Vertex-class (no training) or accept
   the AI Studio terms explicitly.
5. Update the DPDP / consent work (`docs/DEPLOYING.md` legal blockers) to name
   OpenRouter as a sub-processor.
6. Set `OPENROUTER_DATA_POLICY_CONFIRMED=true` — `leadbot check-config` lists the
   provider as unresolved until this is set, and the client logs a warning on
   every start while it is false.

## Key handling

`OPENROUTER_API_KEY` is read from the environment only, lives inside the SDK
client instance, and is never logged, printed, or persisted. Same rule as every
other provider key. `.env` is gitignored; `.env.example` carries a placeholder.

## Policy record (fill in when confirmed)

- Date confirmed: _pending_
- Confirmed by: _pending_
- `google/gemini-3.7-flash` upstream endpoint(s): _pending_
- Upstream retention period: _pending_
- Upstream trains on data (Y/N): _pending_
- Account "allow training providers" setting: _pending_
- `zdr` sent per request (Y/N): _pending_
