"""The Response Guard — hard-rule check on every outbound LLM reply (plan §2 / §3).

`ResponseGuard.check()` is pure and deterministic. The block -> regenerate ->
safe-fallback flow lives in the conversation engine (critique B2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

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
    financing_cleared: bool = False
    stateable_range: str | None = None
    india_compare_range: str | None = None
    premium_countries: list[str] = field(default_factory=list)
    stateable_countries: list[str] = field(default_factory=list)


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

        haystack = f"{ctx.conversation_text}\n{text}"

        # 1. Premium tier in scope -> no figure at all, whatever its value.
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

        # 2. Which approved ranges exist. The stateable tier range is approved
        #    copy in its own right: a figure inside it is allowed whether or not
        #    the sentence happens to name the country. Requiring the country name
        #    here was a false positive — "your ₹30-35 lakh budget works" got
        #    blocked purely for not saying "Kazakhstan", and the lead got the
        #    canned fallback instead of an answer. The India-private range stays
        #    context-gated: outside an India comparison that figure means nothing.
        approved: list[tuple[str, set[str]]] = []
        if ctx.stateable_range:
            tier = "/".join(c for c in ctx.stateable_countries if c) or "stateable"
            approved.append((f"{tier} tier", _digit_runs(ctx.stateable_range)))
        if ctx.india_compare_range and detectors.india_context(haystack):
            approved.append(
                ("India-private comparison", _digit_runs(ctx.india_compare_range))
            )

        if not approved:
            return [
                Violation(
                    "unapproved_cost_figure",
                    ", ".join(figures),
                    "cost figure but no approved cost range is configured "
                    "(plan §2 — nothing may be quoted until one is)",
                )
            ]

        # 3. A country we may not price is in scope -> even an approved-tier
        #    figure would read as *that* country's price.
        unpriceable = detectors.unpriceable_country_mentioned(
            haystack,
            approved_countries=ctx.stateable_countries,
            premium_countries=ctx.premium_countries,
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

        # 4. The actual test: is every figure inside an approved range?
        allowed = set().union(*(digits for _, digits in approved))
        for fig in figures:
            fig_digits = _digit_runs(fig)
            if not fig_digits or not fig_digits.issubset(allowed):
                return [
                    Violation(
                        "cost_outside_approved_range",
                        fig,
                        "figure not within the approved range(s): "
                        + ", ".join(label for label, _ in approved),
                    )
                ]
        return []

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
