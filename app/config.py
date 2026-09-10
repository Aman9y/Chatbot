"""Application configuration.

Every value that Aman has not yet resolved (see docs/plan-critique.md) is
represented here as an explicit optional / sentinel rather than a guessed
default. Downstream code checks for the "unknown" state and refuses to invent
data.
"""

from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models.enums import MinorPolicyStatus


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- runtime -----------------------------------------------------------
    app_env: str = "local"
    log_level: str = "INFO"
    log_json: bool = True

    # --- datastores ------------------------------------------------------
    database_url: str = "postgresql+asyncpg://chatbot:chatbot@localhost:5432/chatbot"
    redis_url: str = "redis://localhost:6379/0"

    # --- Meta WhatsApp Cloud API ---------------------------------------------
    whatsapp_client: Literal["fake", "meta"] = "fake"
    meta_app_secret: str = ""
    meta_verify_token: str = "dev-verify-token"
    meta_access_token: str = ""
    meta_phone_number_id: str = ""
    meta_business_account_id: str = ""
    meta_graph_base_url: str = "https://graph.facebook.com"
    meta_graph_version: str = "v21.0"
    request_timeout_seconds: float = 15.0

    # --- windows / cadence ---------------------------------------------------
    service_window_hours: int = 24
    engagement_push_hours: int = 24
    engagement_handoff_hours: int = 48
    silent_retry_max_rounds: int = 3

    # --- outreach gating (UNRESOLVED: critique A1) --------------------------
    outreach_require_verified_consent: bool = True

    # --- minor policy (UNRESOLVED: critique A2) -----------------------------
    minor_default_policy: MinorPolicyStatus = MinorPolicyStatus.PENDING_REVIEW

    # --- NEET eligibility (confirmed 2026-09-08 by Hamza: 2026 cycle) ------
    neet_year: int | None = 2026
    neet_cutoff_general: int | None = 213
    neet_cutoff_obc: int | None = 175

    # --- per-country stateable cost ranges (confirmed 2026-09-09 by Hamza,
    #     Stellar Educonsultancy). JSON: {"Country": "display range"}. Each range
    #     is bound to its country; the guard never quotes one for another. A
    #     country not listed here (and not premium) -> no figure may be stated.
    country_cost_ranges: str = (
        '{"Uzbekistan": "₹30–35 lakh", "Kyrgyzstan": "₹30–35 lakh", '
        '"Kazakhstan": "₹30–35 lakh", "Russia": "₹27–45 lakh", '
        '"Bangladesh": "₹32–45 lakh", "Georgia": "₹38–55 lakh", '
        '"Nepal": "₹57–80 lakh"}'
    )
    # High-cost countries: figure may ONLY be given with the reason it is higher
    # AND the director's number in the same reply (topic matrix / round-2 rule).
    sensitive_cost_countries: str = "Georgia,Nepal"
    # India-private MBBS range, stateable only as the India-vs-abroad comparison
    # (plan §2 / topic-matrix-2 §6). Kept 2026-09-09 by Hamza.
    india_compare_cost_range: str | None = "₹80L–1.2Cr"

    # --- opt-out keywords --------------------------------------------------
    stop_keywords: str = "<defaults>"

    # --- phone parsing --------------------------------------------------
    default_phone_region: str = "IN"

    # --- per-message cost rate card -------------------------------------
    whatsapp_rate_card: str = "{}"

    # --- conversation engine (Phase 3) -----------------------------------
    llm_provider: Literal["fake", "anthropic", "openai", "gemini", "openrouter"] = "fake"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    anthropic_effort: str = "low"
    anthropic_classifier_model: str = "claude-haiku-4-5"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_classifier_model: str = "gpt-4o-mini"
    # Gemini: key from the GEMINI_API_KEY env var only — required when
    # LLM_PROVIDER=gemini. Free-tier key works for testing with synthetic leads;
    # swap to a paid key via the same var for production, no code change.
    gemini_api_key: str = ""
    # "-latest" aliases track Google's current recommended flash models, so a new
    # key never hits a retired pin. Set a pinned id (e.g. gemini-3.6-flash) in
    # .env for reproducibility.
    gemini_model: str = "gemini-flash-latest"
    gemini_classifier_model: str = "gemini-flash-lite-latest"
    # --- OpenRouter (OpenAI-compatible gateway) -------------------------
    # Key from the OPENROUTER_API_KEY env var only — required when
    # LLM_PROVIDER=openrouter. IMPORTANT: with this provider, lead conversation
    # content is sent to OpenRouter's infrastructure, which then forwards it to
    # the upstream model host (Google, for the gemini route). That is TWO
    # third-party processors in the data path, not one. See
    # docs/llm-data-handling.md — OpenRouter's stated retention/logging policy
    # for API traffic must be confirmed before this points at real leads, and
    # OPENROUTER_DATA_POLICY_CONFIRMED must be set true to acknowledge it.
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "google/gemini-3.7-flash"
    openrouter_classifier_model: str = "google/gemini-3.7-flash"
    # Optional attribution headers OpenRouter shows on its dashboard.
    openrouter_app_url: str = ""
    openrouter_app_title: str = ""
    # --- OpenRouter routing safety pin (enforced in code on every request) ---
    # Restrict upstream routing so lead data can only reach an endpoint we have
    # vetted. Defaults: Google Vertex only (Vertex does not train on API data;
    # AI Studio's terms can), refuse any endpoint that stores data
    # non-transiently, request zero-data-retention, and never silently fall back
    # to another provider. Empty openrouter_provider_only removes the allow-list.
    openrouter_provider_only: str = "google-vertex"
    openrouter_data_collection: Literal["", "allow", "deny"] = "deny"
    openrouter_require_zdr: bool = True
    openrouter_allow_fallbacks: bool = False
    # Operator must flip this true once the routing pin has been proven with a
    # live call that reported the expected upstream provider, AND OpenRouter's
    # data-handling policy has been accepted for lead PII. check-config flags it.
    openrouter_data_policy_confirmed: bool = False
    llm_temperature: float = 0.4
    llm_max_output_tokens: int = 1600
    llm_timeout_seconds: float = 40.0

    bot_autoreply_enabled: bool = True
    conversation_history_turns: int = 12
    kb_path: str = "app/knowledge/kb.yaml"
    kb_retrieval_k: int = 4

    # --- consent + age gate (build-plan §2 / DPDP) -----------------------
    # Master switch. When false the engine treats every lead as gate-cleared
    # (the pre-gate behaviour).
    consent_gate_enabled: bool = True
    # The outbound opt-in drip sweep. OFF by default — needs an approved Meta
    # template and a deliberate operator decision to start contacting leads.
    consent_ask_sweep_enabled: bool = False
    consent_ask_template_name: str = "gate_consent_v1"
    consent_ask_template_language: str = "en"
    consent_ask_template_category: str = "marketing"
    # WABA warm-up: how many opt-in asks one sweep tick sends (a natural drip).
    consent_asks_per_sweep: int = 25
    # Resend the opt-in ask this many times (spaced) before giving up -> DORMANT.
    consent_ask_max_rounds: int = 2
    consent_ask_gap_hours: float = 24.0

    # --- turn dispatch / concurrency (build-plan §3) ----------------------
    # "celery": webhook acks Meta immediately, then a Celery task processes the
    #           turn with a debounce window + per-lead Redis lock (production).
    # "inline": the webhook runs the engine in-request, one message at a time
    #           (dev / tests / `leadbot simulate`).
    webhook_conversation_dispatch: Literal["inline", "celery"] = "inline"
    # After the webhook acks, wait this long before processing so rapid-fire
    # messages from the same lead merge into one turn (build-plan §3.2).
    turn_debounce_ms: int = 500
    # Per-lead lock held from just before LLM processing until the outbound send
    # completes (build-plan §3.3). TTL is the safety release if a worker dies.
    turn_lock_ttl_seconds: int = 120
    # Celery: how many times / how long a queued turn waits for the lock.
    turn_lock_max_retries: int = 15
    turn_lock_retry_seconds: float = 3.0

    # --- Response Guard (Phase 4) --------------------------------------
    guard_enabled: bool = True
    guard_llm_critic_enabled: bool = False
    guard_max_reply_chars: int = 700
    guard_max_reply_words: int = 90
    guard_regenerate_attempts: int = 1

    # --- booking / handoff ---------------------------------------------
    booking_detection_enabled: bool = True
    counselor_webhook_url: str = ""

    # --- scheduler / re-engagement (Phase 5 + 6) -----------------------
    celery_broker_url: str = ""  # blank -> derived from redis_url
    celery_result_backend: str = ""
    scheduler_enabled: bool = True
    sweep_interval_seconds: int = 300
    scheduler_batch_size: int = 200
    scheduler_lock_ttl_seconds: int = 280

    # in-window nudges (0-24h push phase)
    nudge_after_hours: float = 6.0
    nudge_max_per_window: int = 1

    # SILENT re-open rounds (plan §5 / Phase 6 cold-lead retry)
    reengagement_spacing_hours: str = "20,48,72"  # per-round delay, index by round
    reengage_template_name: str = "reengage_v1"
    reengage_template_language: str = "en"
    reengage_template_category: str = "marketing"

    # NURTURE cadence (48h+ no booking)
    nurture_gap_days: float = 4.0
    nurture_max_rounds: int = 2
    nurture_template_name: str = "nurture_v1"
    nurture_template_language: str = "en"
    nurture_template_category: str = "marketing"

    # --- system-prompt placeholders (plan §7 / docs/system-prompt.md) --
    # Unset values render as safe generic phrasing ("our team" / "our counselor").
    company_name: str = "Stellar Educonsultancy"
    # The bot's own identity. It presents as this name and as "she"/"her"
    # (bot_pronoun_*). "" -> falls back to a generic "the assistant".
    bot_name: str = "Stellar AI"
    bot_pronoun_subject: str = "she"
    bot_pronoun_possessive: str = "her"
    counselor_name: str = "Rafique Shaikh"
    # The bot may give this out in Georgia/Nepal cost replies and when a lead
    # asks to talk to someone. "" -> the bot never shares a number.
    counselor_phone: str = "+91 74478 67887"
    office_address: str = (
        "A Wing 302, 2nd Floor, Shanti Shopping Center, near Mira Road Railway "
        "Station, Mira Road East, Thane 401107"
    )
    maps_link: str = ""  # [MISSING] not supplied by client
    booking_link: str = ""  # no self-serve booking
    bot_languages: str = "English, Hindi, Hinglish"
    premium_cost_countries: str = "Germany,United Kingdom,UK,United States,US,USA,Canada,Australia"

    # Default per-lead financing disclosure state (plan §2: default is "do not mention").
    financing_cleared_default: bool = False

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalise_database_url(cls, v: object) -> object:
        """Accept the plain URLs that hosts (Railway, Render, Heroku, Fly) inject
        and coerce them to the async drivers SQLAlchemy needs. Without this a
        ``postgresql://…`` DATABASE_URL loads the sync psycopg2 dialect and
        ``create_async_engine`` raises "the asyncio extension requires an async
        driver". Idempotent — an already-correct URL is returned unchanged.
        """
        if not isinstance(v, str) or not v.strip():
            return v
        url = v.strip()
        # scheme swaps: postgres:// (legacy), postgresql://, +psycopg2, +psycopg
        for prefix in (
            "postgres://",
            "postgresql://",
            "postgresql+psycopg2://",
            "postgresql+psycopg://",
        ):
            if url.startswith(prefix):
                url = "postgresql+asyncpg://" + url[len(prefix):]
                break
        if url.startswith("sqlite://") and "+aiosqlite" not in url:
            url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)
        # asyncpg rejects libpq-only query params. sslmode=require|verify-* ->
        # ask SQLAlchemy's asyncpg dialect for TLS via ?ssl=true instead.
        if url.startswith("postgresql+asyncpg://") and "sslmode=" in url:
            import re as _re

            want_ssl = bool(
                _re.search(r"sslmode=(require|verify-ca|verify-full|prefer|allow)", url)
            )
            url = _re.sub(r"([?&])sslmode=[^&]*", r"\1", url)
            url = _re.sub(r"[?&](channel_binding|gssencmode|target_session_attrs)=[^&]*", "", url)
            url = url.replace("?&", "?").replace("&&", "&").rstrip("?&")
            if want_ssl and "ssl=" not in url:
                url += ("&" if "?" in url else "?") + "ssl=true"
        return url

    @field_validator("minor_default_policy", mode="before")
    @classmethod
    def _blank_policy_to_default(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            return MinorPolicyStatus.PENDING_REVIEW
        return v

    @field_validator("neet_year", "neet_cutoff_general", "neet_cutoff_obc", mode="before")
    @classmethod
    def _blank_int_to_none(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return v

    @field_validator("india_compare_cost_range", mode="before")
    @classmethod
    def _blank_str_to_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    # --- derived helpers ------------------------------------------------
    @property
    def country_cost_range_display(self) -> dict[str, str]:
        """{country: "₹30–35 lakh"} — the approved display strings."""
        try:
            raw = json.loads(self.country_cost_ranges or "{}")
        except json.JSONDecodeError:
            return {}
        return {str(k).strip(): str(v).strip() for k, v in raw.items() if str(v).strip()}

    @property
    def country_cost_bounds(self) -> dict[str, tuple[float, float]]:
        """{country: (low_lakh, high_lakh)} — parsed numeric bounds."""
        from app.money import range_to_lakh

        out: dict[str, tuple[float, float]] = {}
        for country, disp in self.country_cost_range_display.items():
            bounds = range_to_lakh(disp)
            if bounds:
                out[country] = bounds
        return out

    @property
    def sensitive_cost_country_list(self) -> list[str]:
        return [c.strip() for c in self.sensitive_cost_countries.split(",") if c.strip()]

    @property
    def india_compare_bounds(self) -> tuple[float, float] | None:
        from app.money import range_to_lakh

        if not self.india_compare_cost_range:
            return None
        return range_to_lakh(self.india_compare_cost_range)

    @property
    def premium_cost_country_list(self) -> list[str]:
        return [c.strip() for c in self.premium_cost_countries.split(",") if c.strip()]

    @property
    def language_list(self) -> list[str]:
        return [s.strip() for s in self.bot_languages.split(",") if s.strip()]

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    @property
    def reengagement_spacing(self) -> list[float]:
        out: list[float] = []
        for token in self.reengagement_spacing_hours.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                out.append(float(token))
            except ValueError:
                continue
        return out or [20.0, 48.0, 72.0]

    def reengagement_delay_hours(self, round_index: int) -> float:
        spacing = self.reengagement_spacing
        return spacing[min(round_index, len(spacing) - 1)]

    @property
    def silent_retry_max_rounds_effective(self) -> int:
        # a round exists per configured spacing entry, capped by the explicit max
        return min(self.silent_retry_max_rounds, len(self.reengagement_spacing))

    @property
    def rate_card(self) -> dict[str, Decimal]:
        try:
            raw = json.loads(self.whatsapp_rate_card or "{}")
        except json.JSONDecodeError:
            return {}
        out: dict[str, Decimal] = {}
        for key, value in raw.items():
            if key == "_currency":
                continue
            try:
                out[key] = Decimal(str(value))
            except (ArithmeticError, ValueError):
                continue
        return out

    @property
    def rate_card_currency(self) -> str:
        try:
            raw = json.loads(self.whatsapp_rate_card or "{}")
        except json.JSONDecodeError:
            return "INR"
        return str(raw.get("_currency", "INR"))

    @property
    def unresolved_phase1_items(self) -> list[str]:
        """Human-readable list of Phase-1 decisions still outstanding."""
        items: list[str] = []
        if self.neet_year is None:
            items.append("NEET_YEAR (which cycle the ~1,200 leads sat) - critique B9")
        if self.neet_cutoff_general is None or self.neet_cutoff_obc is None:
            items.append("NEET_CUTOFF_GENERAL / NEET_CUTOFF_OBC - critique B9")
        if self.outreach_require_verified_consent and not self.consent_ask_sweep_enabled:
            items.append(
                "Legacy-lead consent: the in-chat opt-in + age gate is the path through "
                "OUTREACH_REQUIRE_VERIFIED_CONSENT, but CONSENT_ASK_SWEEP_ENABLED=false so "
                "no opt-in asks are going out yet (needs the approved gate_consent template "
                "+ an operator decision). Substantive DPDP/legacy-consent policy still open "
                "(critique A1)."
            )
        if self.minor_default_policy in (
            MinorPolicyStatus.PENDING_REVIEW,
            MinorPolicyStatus.BLOCKED,
        ):
            items.append(
                "Minor/DPDP policy undecided - "
                f"MINOR_DEFAULT_POLICY={self.minor_default_policy.value} "
                "blocks outreach to detected minors (critique A2)"
            )
        if not self.country_cost_bounds:
            items.append(
                "COUNTRY_COST_RANGES is empty or unparseable - the bot can quote no figures"
            )
        if not self.maps_link:
            items.append("MAPS_LINK [MISSING] - office-visit replies have no map link")
        _provider_key = {
            "anthropic": ("ANTHROPIC_API_KEY", self.anthropic_api_key),
            "openai": ("OPENAI_API_KEY", self.openai_api_key),
            "gemini": ("GEMINI_API_KEY", self.gemini_api_key),
            "openrouter": ("OPENROUTER_API_KEY", self.openrouter_api_key),
        }.get(self.llm_provider)
        if _provider_key and not _provider_key[1]:
            items.append(
                f"LLM_PROVIDER={self.llm_provider} but {_provider_key[0]} is not set "
                "- conversation engine will error"
            )
        if self.llm_provider == "openrouter" and not self.openrouter_data_policy_confirmed:
            items.append(
                "LLM_PROVIDER=openrouter routes lead conversation data through "
                "OpenRouter's infrastructure (then Google's) - a new third-party "
                "processor. Read OpenRouter's data retention/logging policy for API "
                "traffic, then set OPENROUTER_DATA_POLICY_CONFIRMED=true. See "
                "docs/llm-data-handling.md."
            )
        return items


@lru_cache
def get_settings() -> Settings:
    return Settings()
