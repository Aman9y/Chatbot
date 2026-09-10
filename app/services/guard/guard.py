"""The Response Guard — hard-rule check on every outbound LLM reply (plan §2 / §3).

`ResponseGuard.check()` is pure and deterministic. The block -> regenerate ->
safe-fallback flow lives in the conversation engine (critique B2).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from app import money
from app.config import Settings
from app.services.guard import detectors


def _digit_runs(value: str) -> set[str]:
    """Distinct digit runs in a figure or range: '₹30–35 lakh' -> {'30', '35'}."""

    return set(re.findall(r"\d+", value))


@dataclass(frozen=True)
class Violation:
    rule: str
    matched: str
    detail: str
    severity: str = "hard"


@dataclass
class GuardContext:
    """Everything the guard needs beyond the draft text itself."""

    conversation_text: str = ""
    # the lead's latest inbound message on its own — used to tell "the bot is
    # echoing the budget the lead just stated" from "the bot is quoting a range".
    lead_message: str = ""
    # recent bot side of the conversation (for the safe-fallback no-repeat check)
    prior_bot_text: str = ""
    financing_cleared: bool = False
    # {country: (low_lakh, high_lakh)} — approved per-country ranges
    country_bounds: dict[str, tuple[float, float]] = field(default_factory=dict)
    # {country: "₹30–35 lakh"} — display strings for violation detail
    country_display: dict[str, str] = field(default_factory=dict)
    india_compare_bounds: tuple[float, float] | None = None
    india_compare_display: str | None = None
    premium_countries: list[str] = field(default_factory=list)
    # countries whose figure needs the reason + the director's number (Georgia/Nepal)
    sensitive_countries: list[str] = field(default_factory=list)
    counselor_phone: str = ""


@dataclass
class GuardVerdict:
    allowed: bool
    violations: list[Violation]
    checked_text: str

    @property
    def rules(self) -> list[str]:
        return [v.rule for v in self.violations]

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "violations": [
                {"rule": v.rule, "matched": v.matched, "detail": v.detail}
                for v in self.violations
            ],
        }


class ResponseGuard:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def check(self, text: str, *, context: GuardContext | None = None) -> GuardVerdict:
        ctx = context or GuardContext()
        text = text or ""
        violations: list[Violation] = []

        if not text.strip():
            return GuardVerdict(
                allowed=False,
                violations=[Violation("empty_reply", "", "LLM produced an empty reply")],
                checked_text=text,
            )

        violations += self._check_length(text)
        violations += self._check_cost(text, ctx)
        violations += self._check_financing(text, ctx)
        violations += self._check_payment_terms(text)
        violations += self._check_guarantees(text)
        violations += self._check_pg_cost(text)
        violations += self._check_meta_leak(text)

        return GuardVerdict(allowed=not violations, violations=violations, checked_text=text)

    # -- individual rules -------------------------------------------
    def _check_length(self, text: str) -> list[Violation]:
        words = len(re.findall(r"\S+", text))
        if len(text) > self._settings.guard_max_reply_chars:
            return [
                Violation(
                    "reply_too_long",
                    f"{len(text)} chars",
                    f"exceeds {self._settings.guard_max_reply_chars} char cap",
                )
            ]
        if words > self._settings.guard_max_reply_words:
            return [
                Violation(
                    "reply_too_long",
                    f"{words} words",
                    f"exceeds {self._settings.guard_max_reply_words} word cap",
                )
            ]
        return []

    def _check_cost(self, text: str, ctx: GuardContext) -> list[Violation]:
        figures = detectors.find_money(text)
        if not figures:
            return []

        haystack = ctx.conversation_text + "  " + text

        # 1. Premium tier anywhere in scope -> no figure at all, whatever its value.
        premium = detectors.premium_country_mentioned(haystack, ctx.premium_countries)
        if premium:
            return [
                Violation(
                    "premium_cost_disclosure",
                    ", ".join(figures),
                    f"cost figure while {premium} is in scope "
                    "(plan §2: premium costs never stated)",
                )
            ]

        ranged = list(ctx.country_bounds)

        # 2. A known country the reply names that no approved range covers -> the
        #    figure would read as *that* country's price.
        unpriceable = detectors.unpriceable_country_mentioned(
            text, approved_countries=ranged, premium_countries=ctx.premium_countries
        )
        if unpriceable:
            return [
                Violation(
                    "unapproved_cost_figure",
                    ", ".join(figures),
                    f"cost figure while {unpriceable} is in scope — no approved "
                    "range covers that country (plan §2)",
                )
            ]

        named = detectors.countries_named(text, ranged)

        # "The lead said their budget is 30 lakh — is that enough?" The bot
        # confirming that figure against a couple of countries is NOT a
        # range-quoting reply and must not be blocked as multi-country. It counts
        # as an echo only when every figure the reply states was already in the
        # lead's own last message and the reply introduces no range span.
        lead_lakh = {
            round(v, 1)
            for v in money.figures_in_lakh(ctx.lead_message)
            if not math.isnan(v)
        }
        reply_lakh = [v for v in money.figures_in_lakh(text) if not math.isnan(v)]
        echoing_budget = (
            bool(lead_lakh)
            and bool(reply_lakh)
            and not money.has_range_span(text)
            and all(round(v, 1) in lead_lakh for v in reply_lakh)
        )

        # 3. Strict: one country per cost reply. Two named countries + figures
        #    means the lead can't tell which range is which — unless the bot is
        #    only echoing the lead's stated budget.
        if len(named) >= 2 and not echoing_budget:
            return [
                Violation(
                    "multi_country_cost",
                    ", ".join(named),
                    "a cost reply may name only one country — answer one "
                    "country's cost at a time",
                )
            ]

        # 4. Which bound applies.
        if echoing_budget and len(named) != 1:
            country = None
            pool = [ctx.country_bounds[c] for c in named] or list(ctx.country_bounds.values())
            lo = min(b[0] for b in pool)
            hi = max(b[1] for b in pool)
            label = (
                f"budget check against {', '.join(named)}"
                if named
                else f"general tier envelope (₹{lo:g}–{hi:g} lakh)"
            )
        elif named:
            country = named[0]
            lo, hi = ctx.country_bounds[country]
            label = f"{country} {ctx.country_display.get(country, '')}".strip()
        elif ctx.india_compare_bounds and detectors.india_context(haystack):
            country = None
            lo, hi = ctx.india_compare_bounds
            label = f"India-private comparison {ctx.india_compare_display or ''}".strip()
        elif ctx.country_bounds:
            # figure with no country and no India context: only the overall
            # tier envelope is defensible — the lead can't misattribute it.
            country = None
            lo = min(b[0] for b in ctx.country_bounds.values())
            hi = max(b[1] for b in ctx.country_bounds.values())
            label = f"general tier envelope (₹{lo:g}–{hi:g} lakh)"
        else:
            return [
                Violation(
                    "unapproved_cost_figure",
                    ", ".join(figures),
                    "cost figure but no approved cost range is configured",
                )
            ]

        # 5. Every amount inside [lo, hi]? Range spans ("30-35 lakh") yield both
        #    endpoints; an unreadable amount comes back as nan and fails here.
        for value in money.figures_in_lakh(text):
            if math.isnan(value) or not (lo <= value <= hi):
                return [
                    Violation(
                        "cost_outside_approved_range",
                        ", ".join(figures),
                        f"figure not within the approved range for {label}",
                    )
                ]

        out: list[Violation] = []

        # 6. Georgia / Nepal: the number for the director must be in the reply —
        #    whether the figure is a quoted range or an echo of the lead's budget.
        sensitive_named = country if country in ctx.sensitive_countries else next(
            (c for c in named if c in ctx.sensitive_countries), None
        )
        if sensitive_named and not detectors.reply_offers_number(
            text, ctx.counselor_phone
        ):
            country = sensitive_named
            out.append(
                Violation(
                    "sensitive_cost_needs_contact",
                    country,
                    f"{country} cost stated without offering the director's "
                    "number in the same reply (round-2 rule)",
                )
            )

        # 7. A figure must never be bare — pair it with a concrete inclusion.
        #    The India-comparison reply is about the value gap, not inclusions.
        if country is not None or not (
            ctx.india_compare_bounds and detectors.india_context(haystack)
        ):
            if not detectors.find_cost_inclusions(text):
                out.append(
                    Violation(
                        "cost_missing_inclusion",
                        ", ".join(figures),
                        "cost figure stated bare — pair it with at least one "
                        "concrete inclusion (visa, travel, accommodation, support)",
                    )
                )
        return out

    def _check_payment_terms(self, text: str) -> list[Violation]:
        hits = detectors.find_payment_terms(text)
        if hits:
            return [
                Violation(
                    "payment_terms_disclosure",
                    ", ".join(hits),
                    "states a payment schedule or refund/cancellation term "
                    "(plan §2 rule 7: contractual — never stated in chat)",
                )
            ]
        return []

    def _check_financing(self, text: str, ctx: GuardContext) -> list[Violation]:
        if ctx.financing_cleared:
            return []
        hits = detectors.find_financing(text)
        if hits:
            return [
                Violation(
                    "financing_mention",
                    ", ".join(sorted(set(h.lower() for h in hits))),
                    "financing/loan discussed without per-lead clearance (plan §2)",
                )
            ]
        return []

    def _check_guarantees(self, text: str) -> list[Violation]:
        hits = detectors.find_guarantees(text)
        if hits:
            return [
                Violation(
                    "admission_guarantee",
                    ", ".join(hits),
                    "guarantee/assurance of an outcome "
                    "(plan §2: never guarantee admission/intake/cost)",
                )
            ]
        return []

    def _check_pg_cost(self, text: str) -> list[Violation]:
        hits = detectors.find_pg_cost(text)
        if hits:
            return [
                Violation("pg_cost_mention", hits[0], "PG cost figure is internal-only (plan §2)")
            ]
        return []

    def _check_meta_leak(self, text: str) -> list[Violation]:
        hits = detectors.find_meta_leak(text)
        if hits:
            return [
                Violation(
                    "meta_leak",
                    ", ".join(sorted(set(h.lower() for h in hits))),
                    "reply leaks prompt/template internals or injection text",
                )
            ]
        return []
