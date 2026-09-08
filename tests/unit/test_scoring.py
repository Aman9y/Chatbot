from __future__ import annotations

from app.models.enums import (
    EligibilityFlag,
    InterestTemperature,
    LeadScore,
    LeadUrgency,
    NeetCategory,
)
from app.models.lead import Lead
from app.services.conversation.scoring import ScoreInputs, qualifier_completeness, score_lead


def _lead(**over) -> Lead:
    base = dict(phone_e164="+919812345670")
    base.update(over)
    return Lead(**base)


def _inp(**over) -> ScoreInputs:
    base = dict(
        latest_text="ok",
        inbound_count=3,
        minutes_since_last_bot=5.0,
        booking_detected=False,
        engagement_phase="push",
    )
    base.update(over)
    return ScoreInputs(**base)


def test_qualifier_completeness_counts_only_real_values():
    lead = _lead(
        neet_score=240,
        neet_category=NeetCategory.OBC,
        target_country="Georgia",
        urgency=LeadUrgency.THIS_INTAKE,
        parent_in_loop=True,
    )
    assert qualifier_completeness(lead) == 5


def test_below_cutoff_is_low():
    lead = _lead(eligibility_flag=EligibilityFlag.BELOW_CUTOFF, neet_score=120)
    r = score_lead(lead, _inp())
    assert r.score == LeadScore.LOW
    assert "cutoff" in r.reason


def test_qualified_fast_engaged_lead_is_high():
    lead = _lead(
        eligibility_flag=EligibilityFlag.ABOVE_CUTOFF,
        neet_score=250,
        neet_category=NeetCategory.GENERAL,
        target_country="Georgia",
        urgency=LeadUrgency.THIS_INTAKE,
    )
    r = score_lead(lead, _inp(minutes_since_last_bot=3.0, inbound_count=4))
    assert r.score == LeadScore.HIGH
    assert r.temperature == InterestTemperature.HOT


def test_not_interested_is_low_and_cold():
    lead = _lead(eligibility_flag=EligibilityFlag.ABOVE_CUTOFF, neet_score=250)
    r = score_lead(lead, _inp(latest_text="honestly I'm not interested anymore"))
    assert r.score == LeadScore.LOW
    assert r.temperature == InterestTemperature.COLD
    assert r.not_interested


def test_booking_forces_high():
    lead = _lead()
    r = score_lead(lead, _inp(booking_detected=True))
    assert r.score == LeadScore.HIGH


def test_thin_slow_profile_is_low_or_nurture():
    lead = _lead(eligibility_flag=EligibilityFlag.UNKNOWN)
    r = score_lead(lead, _inp(minutes_since_last_bot=600.0, inbound_count=1))
    assert r.score in (LeadScore.LOW, LeadScore.NURTURE)
    assert r.temperature == InterestTemperature.COLD


def test_partial_profile_mid_temp_is_nurture():
    lead = _lead(
        eligibility_flag=EligibilityFlag.UNKNOWN,
        target_country="Russia",
    )
    r = score_lead(lead, _inp(minutes_since_last_bot=45.0, inbound_count=2))
    assert r.score == LeadScore.NURTURE
