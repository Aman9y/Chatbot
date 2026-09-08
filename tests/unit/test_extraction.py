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


def test_apply_to_lead_is_non_destructive_for_score():
    s = Settings()
    lead = Lead(phone_e164="+919812345670", neet_score=300)
    apply_to_lead(lead, QualifierExtraction(neet_score=120), s)
    assert lead.neet_score == 300  # already known -> not overwritten


def test_apply_to_lead_country_is_last_stated_wins():
    s = Settings()
    lead = Lead(phone_e164="+919812345670", target_country="Russia")
    apply_to_lead(lead, QualifierExtraction(target_country="Georgia"), s)
    assert lead.target_country == "Georgia"
