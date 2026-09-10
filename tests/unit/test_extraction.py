from __future__ import annotations

import pytest

from app.config import Settings
from app.models.enums import EligibilityFlag, LeadUrgency, NeetCategory, RoleHint
from app.models.lead import Lead
from app.services.conversation.extraction import (
    QualifierExtraction,
    apply_to_lead,
    heuristic_extract,
)


@pytest.mark.parametrize(
    "text,score",
    [
        ("I got 240 in NEET", 240),
        ("my neet score is 540", 540),
        ("scored 189 this year", 189),
        ("mera score 320 tha", 320),
    ],
)
def test_extract_neet_score(text, score):
    assert heuristic_extract(text, speaker=RoleHint.UNKNOWN).neet_score == score


@pytest.mark.parametrize(
    "text",
    [
        "is 213 the cutoff for general?",
        "what score do I need, minimum 200?",
        "the qualifying marks are 213 right",
    ],
)
def test_cutoff_questions_are_not_scores(text):
    assert heuristic_extract(text, speaker=RoleHint.UNKNOWN).neet_score is None


def test_extract_category_and_country_and_urgency():
    e = heuristic_extract(
        "I'm OBC, interested in Georgia, want to start this year", speaker=RoleHint.UNKNOWN
    )
    assert e.neet_category == NeetCategory.OBC
    assert e.target_country == "Georgia"
    assert e.urgency == LeadUrgency.THIS_INTAKE


@pytest.mark.parametrize(
    "text,expected",
    [
        ("I'm general category", NeetCategory.GENERAL),
        ("gen", NeetCategory.GENERAL),
        ("unreserved", NeetCategory.GENERAL),
        ("open category", NeetCategory.GENERAL),
        ("samanya", NeetCategory.GENERAL),
        ("we are not reserved", NeetCategory.GENERAL),
        ("OBC", NeetCategory.OBC),
        ("O.B.C.", NeetCategory.OBC),
        ("other backward class", NeetCategory.OBC),
        ("SC", NeetCategory.SC),
        ("scheduled caste", NeetCategory.SC),
        ("I belong to ST", NeetCategory.ST),
        ("scheduled tribe", NeetCategory.ST),
        ("EWS category", NeetCategory.EWS),
        ("economically weaker section", NeetCategory.EWS),
        ("reserved category", NeetCategory.RESERVED),
        ("I'm not general", NeetCategory.RESERVED),
        ("non-general", NeetCategory.RESERVED),
        ("aarakshit", NeetCategory.RESERVED),
        ("SC/ST", NeetCategory.RESERVED),
        ("quota category", NeetCategory.RESERVED),
        ("my category is obc bro", NeetCategory.OBC),
    ],
)
def test_extract_category_many_phrasings(text, expected):
    assert heuristic_extract(text, speaker=RoleHint.UNKNOWN).neet_category == expected


@pytest.mark.parametrize(
    "text",
    ["my friend got 180", "mera cousin ne 190 score kiya", "a classmate scored 210"],
)
def test_third_party_score_is_not_recorded(text):
    assert heuristic_extract(text, speaker=RoleHint.UNKNOWN).neet_score is None


def test_lead_score_extracted_even_when_a_friend_is_mentioned():
    e = heuristic_extract("my friend got 180 but I got 210", speaker=RoleHint.UNKNOWN)
    assert e.neet_score == 210


def test_parent_stating_childs_score_is_kept():
    e = heuristic_extract("my son got 200 in NEET", speaker=RoleHint.PARENT)
    assert e.neet_score == 200


def test_country_aliases():
    assert heuristic_extract("thinking about the UK", speaker=RoleHint.UNKNOWN).target_country == "United Kingdom"


def test_budget_signal():
    assert heuristic_extract("we have a tight budget", speaker=RoleHint.UNKNOWN).budget_band == "tight"
    assert (
        heuristic_extract("budget is not an issue for us", speaker=RoleHint.UNKNOWN).budget_band
        == "flexible"
    )


def test_parent_speaker_sets_parent_in_loop():
    assert heuristic_extract("my son appeared for NEET", speaker=RoleHint.PARENT).parent_in_loop is True


def test_apply_to_lead_fills_and_recomputes_eligibility():
    s = Settings(neet_cutoff_general=213, neet_cutoff_obc=175)
    lead = Lead(phone_e164="+919812345670")
    e = QualifierExtraction(
        neet_score=250, neet_category=NeetCategory.GENERAL, target_country="Georgia"
    )
    changed = apply_to_lead(lead, e, s)
    assert lead.neet_score == 250
    assert lead.neet_category == NeetCategory.GENERAL
    assert lead.target_country == "Georgia"
    assert lead.eligibility_flag == EligibilityFlag.ABOVE_CUTOFF
    assert "eligibility_flag" in changed
    assert lead.qualifiers_updated_at is not None


def test_apply_to_lead_score_is_last_stated_wins_and_re_evaluates():
    # "actually I got 210, not 200" — the corrected score must take effect and
    # re-drive eligibility, not stick with the old one.
    s = Settings(neet_cutoff_general=213, neet_cutoff_obc=175)
    lead = Lead(phone_e164="+919812345670", neet_score=160)
    apply_to_lead(lead, QualifierExtraction(neet_score=160, neet_category=NeetCategory.GENERAL), s)
    assert lead.eligibility_flag == EligibilityFlag.BELOW_CUTOFF
    changed = apply_to_lead(lead, QualifierExtraction(neet_score=230), s)
    assert lead.neet_score == 230
    assert lead.eligibility_flag == EligibilityFlag.ABOVE_CUTOFF
    assert changed["eligibility_flag"] == "above_cutoff"


def test_apply_to_lead_country_is_last_stated_wins():
    s = Settings()
    lead = Lead(phone_e164="+919812345670", target_country="Russia")
    apply_to_lead(lead, QualifierExtraction(target_country="Georgia"), s)
    assert lead.target_country == "Georgia"


# --- NEEDS_CATEGORY: the persistent ambiguous-band state -------------------
def test_ambiguous_band_becomes_needs_category_and_persists():
    s = Settings(neet_cutoff_general=213, neet_cutoff_obc=175)
    lead = Lead(phone_e164="+919812345670")
    # score 200, category unknown -> genuinely undetermined
    apply_to_lead(lead, QualifierExtraction(neet_score=200), s)
    assert lead.eligibility_flag == EligibilityFlag.NEEDS_CATEGORY
    # five unrelated turns — nothing about category — flag must not drift
    for extra in (
        QualifierExtraction(target_country="Georgia"),
        QualifierExtraction(budget_band="tight"),
        QualifierExtraction(urgency=LeadUrgency.THIS_INTAKE),
        QualifierExtraction(city="Pune"),
        QualifierExtraction(parent_in_loop=True),
    ):
        apply_to_lead(lead, extra, s)
        assert lead.eligibility_flag == EligibilityFlag.NEEDS_CATEGORY
    # category finally arrives -> resolves
    changed = apply_to_lead(lead, QualifierExtraction(neet_category=NeetCategory.OBC), s)
    assert lead.eligibility_flag == EligibilityFlag.ABOVE_CUTOFF
    assert changed["eligibility_flag"] == "above_cutoff"


def test_category_before_score_still_resolves():
    s = Settings(neet_cutoff_general=213, neet_cutoff_obc=175)
    lead = Lead(phone_e164="+919812345670")
    apply_to_lead(lead, QualifierExtraction(neet_category=NeetCategory.GENERAL), s)
    assert lead.eligibility_flag == EligibilityFlag.UNKNOWN  # no score yet
    apply_to_lead(lead, QualifierExtraction(neet_score=200), s)
    assert lead.eligibility_flag == EligibilityFlag.BELOW_CUTOFF  # general, 200 < 213


def test_reserved_category_uses_the_relaxed_cutoff():
    s = Settings(neet_cutoff_general=213, neet_cutoff_obc=175)
    lead = Lead(phone_e164="+919812345670", neet_score=200)
    apply_to_lead(lead, QualifierExtraction(neet_score=200, neet_category=NeetCategory.RESERVED), s)
    assert lead.eligibility_flag == EligibilityFlag.ABOVE_CUTOFF


def test_generic_reserved_does_not_overwrite_a_specific_relaxed_category():
    s = Settings()
    lead = Lead(phone_e164="+919812345670", neet_category=NeetCategory.SC)
    apply_to_lead(lead, QualifierExtraction(neet_category=NeetCategory.RESERVED), s)
    assert lead.neet_category == NeetCategory.SC  # RESERVED is less info, not a correction
    # but a real correction to another specific one wins
    apply_to_lead(lead, QualifierExtraction(neet_category=NeetCategory.ST), s)
    assert lead.neet_category == NeetCategory.ST
