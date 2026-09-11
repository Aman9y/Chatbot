import pytest

from app.config import Settings
from app.services.guard.guard import GuardContext, ResponseGuard

_S = Settings()  # real per-country ranges + Stellar identity from config defaults


@pytest.fixture
def guard():
    return ResponseGuard(Settings(guard_max_reply_words=90, guard_max_reply_chars=700))


def _ctx(**over) -> GuardContext:
    base = dict(
        conversation_text="",
        financing_cleared=False,
        country_bounds=dict(_S.country_cost_bounds),
        country_display=dict(_S.country_cost_range_display),
        india_compare_bounds=_S.india_compare_bounds,
        india_compare_display=_S.india_compare_cost_range,
        premium_countries=list(_S.premium_cost_country_list),
        sensitive_countries=list(_S.sensitive_cost_country_list),
        counselor_phone=_S.counselor_phone,
    )
    base.update(over)
    return GuardContext(**base)


@pytest.fixture
def ctx():
    return _ctx()


# --- non-cost rules (unchanged) -------------------------------------
def test_clean_reply_passes(guard, ctx):
    v = guard.check(
        "Great question! Georgia is a popular option. Would a quick call tomorrow work?",
        context=ctx,
    )
    assert v.allowed, v.rules


def test_empty_reply_blocked(guard, ctx):
    assert not guard.check("   ", context=ctx).allowed


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


def test_honest_guarantee_denial_passes_the_guard(guard, ctx):
    # a correct denial must NOT be blocked-and-swapped for the fallback
    v = guard.check(
        "No consultancy can guarantee admission before seeing your profile — "
        "Rafique Sir will give you an honest read on a call.",
        context=ctx,
    )
    assert v.allowed, v.rules


def test_length_blocked(guard, ctx):
    v = guard.check("This is a very wordy reply. " * 20, context=ctx)
    assert not v.allowed
    assert "reply_too_long" in v.rules


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


# --- per-country cost ranges -----------------------------------------
def test_premium_cost_blocked(guard, ctx):
    v = guard.check("Germany works out to about 40 lakh, visa included.", context=ctx)
    assert not v.allowed
    assert "premium_cost_disclosure" in v.rules


def test_premium_country_from_conversation_context(guard, ctx):
    ctx.conversation_text = "user: what does germany cost?"
    v = guard.check(
        "It is roughly 35 lakh for the full package, visa included.", context=ctx
    )
    assert not v.allowed
    assert "premium_cost_disclosure" in v.rules


@pytest.mark.parametrize(
    "country,figure",
    [
        ("Uzbekistan", "₹30–35 lakh"),
        ("Russia", "around ₹27–45 lakh"),
        ("Russia", "about 40 lakh"),          # mid-range now allowed
        ("Bangladesh", "roughly ₹32–45 lakh"),
        ("Kyrgyzstan", "₹30–35 lakh"),
    ],
)
def test_in_range_figure_for_its_country_allowed(guard, country, figure):
    v = guard.check(
        f"For {country} you are looking at {figure} — that covers visa, travel "
        "and accommodation setup. Shall I set up a call?",
        context=_ctx(conversation_text=f"user: what does {country} cost?"),
    )
    assert v.allowed, v.rules


@pytest.mark.parametrize(
    "country,figure",
    [
        ("Uzbekistan", "55 lakh"),            # above the ₹30–35L band
        ("Russia", "60 lakh"),                # above ₹27–45L
        ("Bangladesh", "25 lakh"),            # below ₹32–45L
        ("Kazakhstan", "50 lakh"),            # above Kazakhstan's ₹30–35L
    ],
)
def test_out_of_range_figure_for_its_country_blocked(guard, country, figure):
    v = guard.check(
        f"For {country} it is about {figure}, visa and travel included.",
        context=_ctx(),
    )
    assert not v.allowed
    assert v.rules == ["cost_outside_approved_range"]


def test_strict_one_country_per_cost_reply(guard):
    v = guard.check(
        "Russia is ₹27–45 lakh and Bangladesh ₹32–45 lakh, both visa-inclusive.",
        context=_ctx(),
    )
    assert not v.allowed
    assert "multi_country_cost" in v.rules


def test_unpriceable_country_with_figure_blocked(guard):
    v = guard.check(
        "Armenia works out to about ₹30–35 lakh with visa and travel handled.",
        context=_ctx(),
    )
    assert not v.allowed
    assert "unapproved_cost_figure" in v.rules


# --- round-1 regression: budget figure, no country named ------------
def test_bare_in_envelope_budget_answer_allowed(guard):
    v = guard.check(
        "That budget works — the economical route sits around ₹30–35 lakh, and "
        "that covers visa and accommodation setup. Want a quick call?",
        context=_ctx(conversation_text="what if my budget is 30 lakh"),
    )
    assert v.allowed, v.rules


# --- budget echo: the bot confirming the lead's own stated figure -----
def test_budget_echo_naming_two_cheap_countries_allowed(guard):
    # lead said "30 lakh"; the bot confirms it against two economical countries.
    # Not a range-quoting reply -> must NOT trip multi_country_cost.
    v = guard.check(
        "30 lakh is workable — that comfortably covers Uzbekistan or Kyrgyzstan, "
        "including visa processing and accommodation setup. Which are you leaning "
        "toward?",
        context=_ctx(lead_message="my budget is 30 lakh, is that enough?"),
    )
    assert v.allowed, v.rules


def test_budget_echo_still_bounds_the_figure(guard):
    # lead said "20 lakh" — below every approved range; echo doesn't make it ok.
    v = guard.check(
        "20 lakh would cover Uzbekistan comfortably, visa included.",
        context=_ctx(lead_message="can I do it in 20 lakh?"),
    )
    assert not v.allowed
    assert "cost_outside_approved_range" in v.rules


def test_budget_echo_naming_georgia_still_needs_number(guard):
    v = guard.check(
        "55 lakh could open up Georgia too, with visa and travel handled.",
        context=_ctx(lead_message="what if my budget is 55 lakh?"),
    )
    assert not v.allowed
    assert "sensitive_cost_needs_contact" in v.rules


def test_bot_quoting_two_ranges_is_still_blocked_even_after_a_budget_message(guard):
    # a range span the bot introduced is not an echo, whatever the lead said.
    v = guard.check(
        "Uzbekistan is ₹30–35 lakh and Georgia ₹38–55 lakh, visa included.",
        context=_ctx(lead_message="my budget is around 30 lakh"),
    )
    assert not v.allowed
    assert "multi_country_cost" in v.rules


# --- review notes §1: the guard's job changed — wrong-answer cases -----
def test_wrong_countrys_range_quoted_for_the_country_asked_about(guard):
    # asked about Uzbekistan (₹30–35L), bot quotes Georgia's ₹38–55L band.
    v = guard.check(
        "For Uzbekistan you're looking at about ₹38–55 lakh, visa and travel "
        "included.",
        context=_ctx(conversation_text="user: what does Uzbekistan cost?"),
    )
    assert not v.allowed
    assert "cost_outside_approved_range" in v.rules


def test_blended_range_across_countries_is_blocked(guard):
    # ₹34–60 lakh matches no single approved country — a blended/invented figure.
    v = guard.check(
        "Overall you should plan for around ₹34–60 lakh depending on the country, "
        "with visa and accommodation handled.",
        context=_ctx(),
    )
    assert not v.allowed
    assert "blended_cost_range" in v.rules


def test_countryless_range_that_matches_an_approved_band_is_allowed(guard):
    v = guard.check(
        "The economical route runs about ₹30–35 lakh, and that covers visa "
        "processing and accommodation setup. Want a quick call?",
        context=_ctx(),
    )
    assert v.allowed, v.rules


@pytest.mark.parametrize("country", ["Belarus", "Armenia", "Poland", "Philippines"])
def test_range_for_a_country_not_on_the_approved_list_is_blocked(guard, country):
    v = guard.check(
        f"{country} comes to roughly ₹30–35 lakh with visa and travel handled.",
        context=_ctx(),
    )
    assert not v.allowed
    assert {"unapproved_cost_figure", "premium_cost_disclosure"} & set(v.rules)


def test_nepal_figure_bare_without_number_blocked(guard):
    v = guard.check(
        "Nepal works out to ₹57–80 lakh because it's so close to India and the "
        "academics mirror ours. Visa and travel are handled throughout.",
        context=_ctx(),
    )
    assert not v.allowed
    assert "sensitive_cost_needs_contact" in v.rules


def test_georgia_range_misquoted_low_still_blocked_as_out_of_range(guard):
    # bot low-balls Georgia to the CIS band to make it look cheaper.
    v = guard.check(
        "Georgia is really only about ₹30–35 lakh, visa included — call Rafique "
        "Sir on +91 74478 67887 for the exact figure.",
        context=_ctx(),
    )
    assert not v.allowed
    assert "cost_outside_approved_range" in v.rules


def test_countryless_figure_above_the_whole_envelope_blocked(guard):
    v = guard.check(
        "MBBS abroad is about 95 lakh with everything included.", context=_ctx()
    )
    assert not v.allowed
    assert "cost_outside_approved_range" in v.rules


# --- Georgia / Nepal: figure needs the director's number -----------
def test_georgia_figure_without_number_blocked(guard):
    v = guard.check(
        "Georgia runs ₹38–55 lakh — it is a different education market with its "
        "own cost structure. Visa and travel are handled throughout.",
        context=_ctx(),
    )
    assert not v.allowed
    assert "sensitive_cost_needs_contact" in v.rules


def test_georgia_figure_with_number_allowed(guard):
    v = guard.check(
        "Georgia is ₹38–55 lakh — a separate education market with a higher cost "
        "base. Visa and accommodation are handled. Worth talking it through with "
        "Rafique Sir directly on +91 74478 67887.",
        context=_ctx(),
    )
    assert v.allowed, v.rules


def test_nepal_figure_with_number_and_reason_allowed(guard):
    v = guard.check(
        "Nepal is ₹57–80 lakh — the premium buys proximity to India and "
        "India-aligned academics. We handle visa and travel. Rafique Sir can "
        "walk you through it on +91 74478 67887.",
        context=_ctx(),
    )
    assert v.allowed, v.rules


# --- inclusion pairing --------------------------------------------
def test_bare_figure_without_inclusion_blocked(guard):
    v = guard.check("For Kazakhstan it is ₹30–35 lakh.", context=_ctx())
    assert not v.allowed
    assert "cost_missing_inclusion" in v.rules


def test_figure_with_inclusion_allowed(guard):
    v = guard.check(
        "Kazakhstan is ₹30–35 lakh, and that covers visa processing and travel. "
        "Want the full picture on a call?",
        context=_ctx(),
    )
    assert v.allowed, v.rules


# --- India comparison --------------------------------------------
def test_india_comparison_allowed_in_india_context(guard):
    v = guard.check(
        "Private MBBS in India runs ₹80L–1.2Cr, versus the economical route "
        "abroad. That gap is the whole reason to look abroad — worth a call.",
        context=_ctx(
            conversation_text="user: how does this compare to a private college in India?"
        ),
    )
    assert v.allowed, v.rules


def test_india_range_figure_blocked_without_india_context(guard):
    v = guard.check(
        "It is about ₹80 lakh to 1.2 crore, visa included.",
        context=_ctx(conversation_text="user: what does Georgia cost?"),
    )
    assert not v.allowed


# --- unconfigured -> nothing quotable ---------------------------
def test_no_ranges_configured_blocks_every_figure(guard):
    v = guard.check(
        "It is around 30 lakh with visa included.",
        context=_ctx(country_bounds={}, country_display={}, india_compare_bounds=None),
    )
    assert not v.allowed
    assert "unapproved_cost_figure" in v.rules


def test_pronoun_us_does_not_read_as_premium_country(guard):
    v = guard.check(
        "For Kazakhstan it is ₹30–35 lakh, visa included — let us set up a call.",
        context=_ctx(),
    )
    assert v.allowed, v.rules


# --- director review: same-bound-group cost framing (item 9) ---------------
def test_same_bound_trio_named_together_is_allowed(guard):
    v = guard.check(
        "Uzbekistan, Kazakhstan and Kyrgyzstan all run about ₹30–35 lakh, "
        "covering visa and accommodation — happy to go deeper on any of them.",
        context=_ctx(),
    )
    assert v.allowed, v.rules


def test_same_bound_trio_with_deferred_sensitive_country_mention_is_allowed(guard):
    # Russia/Georgia named with NO figure of their own ("separately priced") —
    # must not trip multi_country_cost or the Georgia mandatory-number rule.
    v = guard.check(
        "Most families land around ₹30–35 lakh with Uzbekistan, Kazakhstan or "
        "Kyrgyzstan, visa and accommodation included. Russia and Georgia have "
        "their own separate pricing — want those details too?",
        context=_ctx(),
    )
    assert v.allowed, v.rules


def test_same_bound_trio_still_blocks_if_a_distinct_figure_is_added(guard):
    # Georgia named WITH its own (different) figure in the same reply — still
    # genuinely blending ranges, must still be blocked.
    v = guard.check(
        "Uzbekistan, Kazakhstan and Kyrgyzstan run about ₹30–35 lakh; Georgia "
        "is ₹38–55 lakh, visa included.",
        context=_ctx(),
    )
    assert not v.allowed


def test_two_countries_with_genuinely_different_bounds_still_blocked(guard):
    # Russia (27-45) and Bangladesh (32-45) don't share an identical bound —
    # naming both with one figure is still genuinely ambiguous blending.
    v = guard.check(
        "Russia and Bangladesh both come to about ₹30 lakh, visa included.",
        context=_ctx(),
    )
    assert not v.allowed
    assert "multi_country_cost" in v.rules
