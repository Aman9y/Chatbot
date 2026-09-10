"""Review notes §3 — the guard must catch overselling, not only leaks.

Every other guard test checks whether the bot leaks forbidden information. These
check the opposite failure: the bot drifting toward comfort — implying admission
is easy, FMGE trivial, outcomes assured, risk nil, safety absolute. For a
17-year-old and an anxious parent making an irreversible ₹30L+ decision, an
overpromise that leads them to commit is worse than a deflection that loses the
lead.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.services.guard.guard import GuardContext, ResponseGuard

_S = Settings()


@pytest.fixture
def guard():
    return ResponseGuard(Settings(guard_max_reply_words=120, guard_max_reply_chars=1000))


@pytest.fixture
def ctx():
    return GuardContext(
        country_bounds=dict(_S.country_cost_bounds),
        country_display=dict(_S.country_cost_range_display),
        india_compare_bounds=_S.india_compare_bounds,
        premium_countries=list(_S.premium_cost_country_list),
        sensitive_countries=list(_S.sensitive_cost_country_list),
        counselor_phone=_S.counselor_phone,
    )


# --- implied admission certainty -------------------------------------
@pytest.mark.parametrize(
    "reply",
    [
        "With your score admission is basically a formality — shall I set up the call?",
        "You'll definitely get in, don't worry about that part.",
        "Your seat is as good as done once you send the documents.",
        "Getting admission there is easy, it's the paperwork that takes time.",
        "You're sure to get in with those marks.",
    ],
)
def test_implied_admission_certainty_blocked(guard, ctx, reply):
    v = guard.check(reply, context=ctx)
    assert not v.allowed
    assert "overpromise" in v.rules


# --- downplaying FMGE difficulty -----------------------------------
@pytest.mark.parametrize(
    "reply",
    [
        "Honestly the FMGE is easy, most students clear it first try.",
        "FMGE isn't that hard if you pick the right university.",
        "The screening exam is basically a formality these days.",
        "You'll clear the FMGE no problem after the internship.",
        "Nothing to worry about with the FMGE, really.",
    ],
)
def test_downplaying_fmge_blocked(guard, ctx, reply):
    v = guard.check(reply, context=ctx)
    assert not v.allowed
    assert "overpromise" in v.rules


# --- guaranteed outcomes ------------------------------------------
@pytest.mark.parametrize(
    "reply",
    [
        "Follow the plan and you're guaranteed to become a doctor.",
        "Your career is secure the moment you finish the degree.",
        "This route means guaranteed placement back in India.",
    ],
)
def test_guaranteed_outcome_blocked(guard, ctx, reply):
    v = guard.check(reply, context=ctx)
    assert not v.allowed
    assert {"overpromise", "admission_guarantee"} & set(v.rules)


# --- minimised risk ----------------------------------------------
@pytest.mark.parametrize(
    "reply",
    [
        "There's no real risk here, thousands of students do this every year.",
        "Nothing can go wrong if you go through us.",
        "It's a risk-free route, honestly.",
        "You have nothing to lose by starting the process now.",
    ],
)
def test_minimised_risk_blocked(guard, ctx, reply):
    v = guard.check(reply, context=ctx)
    assert not v.allowed
    assert "overpromise" in v.rules


# --- over-reassurance on safety ----------------------------------
@pytest.mark.parametrize(
    "reply",
    [
        "Georgia is 100% safe, even for girls.",
        "It's completely safe there, nothing to worry about.",
        "You'll be completely fine, the city is totally safe.",
    ],
)
def test_over_reassurance_on_safety_blocked(guard, ctx, reply):
    v = guard.check(reply, context=ctx)
    assert not v.allowed
    assert "overpromise" in v.rules


# --- honest reassurance must still pass (the line is thin) -------------
@pytest.mark.parametrize(
    "reply",
    [
        "The FMGE is a real exam, but it's more manageable than most people "
        "assume when the university is a strong one — that's what Rafique Sir "
        "helps you get right.",
        "Many students clear the FMGE; how they do depends a lot on where they "
        "studied. Worth talking through on a call.",
        "It's often safer than students expect, though it genuinely varies by "
        "city — the counsellor gives you a candid view for each option.",
        "No honest consultancy can promise admission before seeing your profile; "
        "the counsellor will assess your case properly.",
        "You'll get admission support from us throughout the process.",
        "There's a real risk of delays if the documents come in late, so we "
        "start early.",
    ],
)
def test_honest_reassurance_still_passes(guard, ctx, reply):
    v = guard.check(reply, context=ctx)
    assert v.allowed, v.rules
