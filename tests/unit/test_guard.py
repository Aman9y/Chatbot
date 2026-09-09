import pytest

from app.config import Settings
from app.services.guard.guard import GuardContext, ResponseGuard


@pytest.fixture
def guard():
    return ResponseGuard(Settings(guard_max_reply_words=90, guard_max_reply_chars=700))


@pytest.fixture
def ctx():
    return GuardContext(
        conversation_text="",
        financing_cleared=False,
        stateable_range=None,
        premium_countries=["Germany", "UK", "United States"],
        stateable_countries=["Kazakhstan", "Uzbekistan"],
    )


def test_clean_reply_passes(guard, ctx):
    v = guard.check(
        "Great question! Georgia is a popular option. Would a quick call tomorrow work?",
        context=ctx,
    )
    assert v.allowed
    assert v.violations == []


def test_empty_reply_blocked(guard, ctx):
    assert not guard.check("   ", context=ctx).allowed


def test_premium_cost_blocked(guard, ctx):
    v = guard.check("Germany works out to about 40 lakh in total.", context=ctx)
    assert not v.allowed
    assert "premium_cost_disclosure" in v.rules


def test_premium_country_from_conversation_context(guard, ctx):
    ctx.conversation_text = "user: what does germany cost?"
    v = guard.check("It's roughly 35 lakh for the full package.", context=ctx)
    assert not v.allowed
    assert "premium_cost_disclosure" in v.rules


def test_any_cost_figure_blocked_when_range_unset(guard, ctx):
    v = guard.check("Kazakhstan is about 30 lakh total.", context=ctx)
    assert not v.allowed
    assert "unapproved_cost_figure" in v.rules


def test_cost_within_configured_range_allowed():
    guard = ResponseGuard(Settings(stateable_cost_range="30-35 lakh"))
    ctx = GuardContext(
        conversation_text="user: kazakhstan cost?",
        stateable_range="30-35 lakh",
        premium_countries=["Germany"],
        stateable_countries=["Kazakhstan"],
    )
    v = guard.check("For Kazakhstan it's typically in the 30-35 lakh range.", context=ctx)
    assert v.allowed


def _range_ctx(**over) -> GuardContext:
    base = dict(
        conversation_text="",
        stateable_range="₹30–35 lakh",
        india_compare_range="₹80L–1.2Cr",
        premium_countries=["Germany", "UK", "United States"],
        stateable_countries=["Kazakhstan", "Uzbekistan"],
    )
    base.update(over)
    return GuardContext(**base)


def test_india_comparison_figure_allowed_in_india_context(guard):
    ctx = _range_ctx(conversation_text="user: how does this compare to a private college in India?")
    v = guard.check(
        "Private MBBS in India runs around ₹80L–1.2Cr, versus roughly ₹30–35 lakh "
        "for the Kazakhstan tier. Want to get the real numbers on a call?",
        context=ctx,
    )
    assert v.allowed, v.rules


def test_india_range_figure_blocked_without_india_context(guard):
    ctx = _range_ctx(conversation_text="user: what does Georgia cost?")
    v = guard.check("It's about ₹80 lakh to 1.2 crore.", context=ctx)
    assert not v.allowed
    assert "unapproved_cost_figure" in v.rules or "cost_outside_approved_range" in v.rules


def test_kyrgyzstan_figure_blocked(guard):
    ctx = _range_ctx(conversation_text="user: kyrgyzstan fees?")
    v = guard.check("Kyrgyzstan is around 32 lakh all in.", context=ctx)
    assert not v.allowed


def test_payment_terms_blocked(guard, ctx):
    v = guard.check(
        "The token amount is non-refundable, but the tuition advance can be "
        "refunded within 15 days.",
        context=ctx,
    )
    assert not v.allowed
    assert "payment_terms_disclosure" in v.rules


def test_payment_terms_deflection_allowed(guard, ctx):
    v = guard.check(
        "Refund and payment terms are exactly the kind of thing the counsellor "
        "puts in writing and explains on the call — shall I set one up?",
        context=ctx,
    )
    assert v.allowed, v.rules


def test_financing_blocked_unless_cleared(guard, ctx):
    assert not guard.check("We can arrange an education loan for you.", context=ctx).allowed
    ctx.financing_cleared = True
    assert guard.check(
        "The counsellor will walk you through the loan option on the call.", context=ctx
    ).allowed


def test_guarantee_blocked(guard, ctx):
    v = guard.check("Your admission is guaranteed if you book now.", context=ctx)
    assert not v.allowed
    assert "admission_guarantee" in v.rules


def test_length_blocked(guard, ctx):
    long_reply = "This is a very wordy reply. " * 20
    v = guard.check(long_reply, context=ctx)
    assert not v.allowed
    assert "reply_too_long" in v.rules


# --- approved-range figures must not be blocked for lacking a country name ---
# Regression: _check_cost used to require a stateable COUNTRY NAME in the text
# before the approved range applied at all, so a figure squarely inside
# ₹30–35 lakh fell through to `unapproved_cost_figure` and the lead got the
# canned safe-fallback instead of an answer.


def test_approved_range_figure_allowed_without_country_named(guard):
    ctx = _range_ctx(conversation_text="what if my budget is 30 lakh")
    v = guard.check(
        "That's a workable budget — ₹30–35 lakh covers the more economical "
        "route. Shall I set up a quick call?",
        context=ctx,
    )
    assert v.allowed, v.rules


def test_plain_echo_of_in_range_budget_allowed(guard):
    ctx = _range_ctx(conversation_text="what if my budget is 30 lakh")
    v = guard.check("30 lakh is realistic for the economical tier.", context=ctx)
    assert v.allowed, v.rules


def test_out_of_range_figure_is_distinguishable_from_unconfigured(guard):
    """An out-of-range figure must report the RANGE rule, not the catch-all —
    otherwise a good-but-unquotable answer and a genuinely bad one are
    indistinguishable in the trace."""

    ctx = _range_ctx()
    v = guard.check("You'd be looking at about 55 lakh all in.", context=ctx)
    assert not v.allowed
    assert v.rules == ["cost_outside_approved_range"]


def test_pronoun_us_does_not_read_as_premium_country(guard):
    ctx = _range_ctx()
    v = guard.check(
        "₹30–35 lakh is the usual range — let us set up a call.", context=ctx
    )
    assert v.allowed, v.rules


@pytest.mark.parametrize(
    "draft,conversation",
    [
        ("Kyrgyzstan is around ₹30–35 lakh.", "kyrgyzstan fees?"),
        ("Georgia works out to about ₹30–35 lakh.", ""),
        ("That's about ₹30–35 lakh.", "user: what does Russia cost?"),
    ],
)
def test_approved_figure_still_blocked_beside_an_unpriceable_country(
    guard, draft, conversation
):
    """An approved-tier figure must not ride alongside a country no approved
    range covers — the lead would read it as that country's price."""

    v = guard.check(draft, context=_range_ctx(conversation_text=conversation))
    assert not v.allowed
    assert "unapproved_cost_figure" in v.rules
