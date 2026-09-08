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

    # --- stateable cost tiers (confirmed 2026-09-08 by Hamza) -------------
    # Kazakhstan + Uzbekistan only. Kyrgyzstan is NOT confirmed yet — it is
    # deliberately absent so the guard treats any Kyrgyzstan figure as unstated.
    stateable_cost_countries: str = "Kazakhstan,Uzbekistan"
    stateable_cost_range: str | None = "₹30–35 lakh"
    # India-private MBBS range, stateable only as the India-vs-abroad comparison
    # (plan §2 / topic-matrix-2 §6). Confirmed 2026-09-08 by Hamza.
    india_compare_cost_range: str | None = "₹80L–1.2Cr"

    # --- opt-out keywords --------------------------------------------------
    stop_keywords: str = "<defaults>"

    # --- phone parsing --------------------------------------------------
    default_phone_region: str = "IN"

    # --- per-message cost rate card -------------------------------------
    whatsapp_rate_card: str = "{}"

    # --- conversation engine (Phase 3) -----------------------------------
    llm_provider: Literal["fake", "anthropic", "openai"] = "fake"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"
    anthropic_effort: str = "low"
    anthropic_classifier_model: str = "claude-haiku-4-5"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_classifier_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.4
    llm_max_output_tokens: int = 1600
    llm_timeout_seconds: float = 40.0

    bot_autoreply_enabled: bool = True
    conversation_history_turns: int = 12
    kb_path: str = "app/knowledge/kb.yaml"
    kb_retrieval_k: int = 4

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
    company_name: str = ""
    counselor_name: str = ""
    office_address: str = ""
    maps_link: str = ""
    booking_link: str = ""
    bot_languages: str = "English, Hindi, Hinglish"
    premium_cost_countries: str = "Germany,United Kingdom,UK,United States,US,USA,Canada,Australia"

    # Default per-lead financing disclosure state (plan §2: default is "do not mention").
    financing_cleared_default: bool = False

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

    @field_validator("stateable_cost_range", "india_compare_cost_range", mode="before")
    @classmethod
    def _blank_str_to_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    # --- derived helpers ------------------------------------------------
    @property
    def stateable_cost_country_list(self) -> list[str]:
        return [c.strip() for c in self.stateable_cost_countries.split(",") if c.strip()]

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
        if self.outreach_require_verified_consent:
            items.append(
                "Consent audit of legacy leads not done - OUTREACH_REQUIRE_VERIFIED_CONSENT=true "
                "blocks all outreach to imported leads (critique A1)"
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
        if self.stateable_cost_range is None:
            items.append("STATEABLE_COST_RANGE (Kazakhstan/Uzbekistan tier figure) - plan s2")
        if not self.company_name or not self.counselor_name:
            items.append(
                "COMPANY_NAME / COUNSELOR_NAME unset - system prompt renders generic "
                "phrasing ('our team' / 'our counselor') - plan s7"
            )
        if self.llm_provider != "fake" and not (
            self.anthropic_api_key or self.openai_api_key
        ):
            items.append(
                f"LLM_PROVIDER={self.llm_provider} but no API key set "
                "(ANTHROPIC_API_KEY / OPENAI_API_KEY) - conversation engine will error"
            )
        return items


@lru_cache
def get_settings() -> Settings:
    return Settings()
