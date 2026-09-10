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

## Code-level routing pin (enforced on every request)

Rather than rely on account defaults, `OpenRouterLLMClient` attaches provider-
routing constraints to every call, built from `Settings.openrouter_*`:

```
provider.only            = ["google-vertex"]   # Vertex does not train on API data
provider.data_collection = "deny"              # refuse non-transient storage
provider.allow_fallbacks = false               # error, never silently reroute
zdr                      = true                # request zero-data-retention
```

If OpenRouter cannot satisfy the pin it returns a 404 and the turn falls back —
lead data cannot reach an un-vetted endpoint. Relax via `OPENROUTER_PROVIDER_ONLY`
etc. only with a documented reason.

**Do not** add `provider.require_parameters` — the Vertex endpoint for
`google/gemini-3.7-flash` does not advertise `temperature`, so requiring it would
push routing back to AI Studio (or fail).

## Live-call proof of the pin (2026-09-10)

Verified against the live API with the real client:

- **`provider.only` is enforced** — a request pinned to a bogus provider slug with
  `allow_fallbacks:false` returns
  `404 "No allowed providers are available… Providers serving
  google/gemini-3.7-flash: google-ai-studio, google-vertex"`. So the allow-list is
  parsed and honoured, and exactly two endpoints serve this model.
- **`GET /models/google/gemini-3.7-flash/endpoints`** maps
  `provider_name:"Google"` ↔ `tag: google-vertex/*`, and
  `provider_name:"Google AI Studio"` ↔ `tag: google-ai-studio/*`.
- A request pinned to `google-ai-studio` returns `provider: "Google AI Studio"`;
  our production pin (`google-vertex` + `deny` + `zdr` + no-fallback) returns
  **`provider: "Google"`** (i.e. `google-vertex/*`) on every call, and **succeeds**
  — meaning the Vertex endpoint also satisfies `data_collection:deny` and the ZDR
  request (or `allow_fallbacks:false` would have hard-failed).
- `is_byok: false` — OpenRouter's own Google credentials, not ours.

Conclusion: with the pin in place, `google/gemini-3.7-flash` traffic routes to
**Google Vertex AI**, which under the Google Cloud terms does not use API
inputs/outputs for model training. `OPENROUTER_DATA_POLICY_CONFIRMED` set `true`
on this basis.

## Model-behaviour notes on this route (not blockers)

- `gemini-3.7-flash` is a **reasoning model**; hidden reasoning tokens count
  against `max_tokens` and are billed. With a tight cap the visible completion
  can be empty (the guard treats that as `empty_reply` → regenerate → fallback,
  so it fails safe). Prod's `LLM_MAX_OUTPUT_TOKENS=1600` has ample headroom.
- The Vertex endpoint does **not apply `temperature`** (not in its
  `supported_parameters`); replies run at the model default regardless of
  `LLM_TEMPERATURE`.

## Before this points at real leads — checklist

1. ~~Confirm the upstream endpoint is Vertex-class~~ — done via the code pin +
   live-call proof above (2026-09-10).
2. Set the OpenRouter account to **disallow providers that may train on data**
   (Privacy settings) as defence-in-depth behind the per-request pin. *(operator
   task — still recommended)*
3. **Update the DPDP / consent work (`docs/DEPLOYING.md` legal blockers) to name
   OpenRouter as a sub-processor.** *(still open — legal track)*
4. Keep an eye on cost: reasoning tokens are billed on this route (see notes).

Done: `OPENROUTER_DATA_POLICY_CONFIRMED=true` (2026-09-10). `leadbot check-config`
no longer flags the provider; the client no longer logs the warning.

## Key handling

`OPENROUTER_API_KEY` is read from the environment only, lives inside the SDK
client instance, and is never logged, printed, or persisted. Same rule as every
other provider key. `.env` is gitignored; `.env.example` carries a placeholder.

## Policy record

- Date confirmed: 2026-09-10
- Confirmed by: Hamza (routing pin + live-call proof; see "Live-call proof" above)
- `google/gemini-3.7-flash` upstream endpoint (pinned): **`google-vertex`**
  (OpenRouter label "Google", endpoint tag `google-vertex/global/flex`);
  `google-ai-studio` is available but **excluded by the pin**.
- Upstream trains on data: **No** — Google Vertex AI, under the Google Cloud
  terms, does not use API inputs/outputs for model training.
- Upstream retention period: not exposed via OpenRouter's API (`data_policy: {}`);
  governed by Google Cloud's DPA (transient / abuse-monitoring only). Re-confirm
  on the OpenRouter model page UI if a written figure is needed for the DPDP work.
- OpenRouter content logging: OFF by default; not opted in. Metadata retained.
- `provider.data_collection`: `deny` (sent every request, accepted by Vertex).
- `zdr` sent per request: **Yes**.
- Account "allow training providers" setting: _operator to set to disallow as
  defence-in-depth (item 2 in the checklist)._
- Still open: name OpenRouter as a sub-processor in the DPDP / records-of-
  processing work.
