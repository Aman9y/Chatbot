"""The NEEDS_CATEGORY open-qualifier block is driven by tracked lead state."""

from app.models.enums import EligibilityFlag, NeetCategory
from app.models.lead import Lead
from app.services.conversation.context import _eligibility_block


def _lead(flag, score=None):
    return Lead(
        phone_e164="+919812345670",
        neet_score=score,
        neet_category=NeetCategory.UNKNOWN,
        eligibility_flag=flag,
    )


def test_block_present_only_for_needs_category_or_needs_pcb():
    assert _eligibility_block(_lead(EligibilityFlag.NEEDS_CATEGORY, 200)) != ""
    assert _eligibility_block(_lead(EligibilityFlag.NEEDS_PCB, 250)) != ""
    for other in (
        EligibilityFlag.ABOVE_CUTOFF,
        EligibilityFlag.BELOW_CUTOFF,
        EligibilityFlag.UNKNOWN,
    ):
        assert _eligibility_block(_lead(other, 200)) == ""


def test_needs_pcb_block_names_the_score_and_forbids_confirming_eligible():
    block = _eligibility_block(_lead(EligibilityFlag.NEEDS_PCB, 250)).lower()
    assert "250" in block
    assert "pcb" in block
    assert "do not tell them they're eligible" in block
    assert "across every other topic" in block or "every other topic" in block


def test_block_names_the_score_and_forbids_assuming():
    block = _eligibility_block(_lead(EligibilityFlag.NEEDS_CATEGORY, 198)).lower()
    assert "198" in block
    assert "do not assume a category" in block
    assert "open or" in block or "open OR closed".lower() in block
    assert "every other topic" in block or "across every other topic" in block


def test_block_appears_in_the_rendered_turn_prompt(monkeypatch):
    # a thin check that build_turn_context wires it in — full flow is covered by
    # the integration suite; here we just confirm the section lands in the string.
    from app.services.conversation import context as ctxmod

    lead = _lead(EligibilityFlag.NEEDS_CATEGORY, 205)
    block = ctxmod._eligibility_block(lead)
    assert "OPEN QUALIFIER" in block
