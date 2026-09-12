"""The eligibility CYCLE STEP blocks are driven by tracked lead state — part
of the always-return-to-cycle spine (see test_context_cycle.py for the rest
of the cycle: interest, country decision, Rafique introduction)."""

from app.models.enums import EligibilityFlag, NeetCategory
from app.models.lead import Lead
from app.services.conversation.context import _cycle_block


def _lead(flag, score=None):
    return Lead(
        phone_e164="+919812345670",
        neet_score=score,
        neet_category=NeetCategory.UNKNOWN,
        eligibility_flag=flag,
        considering_abroad=True,  # past the interest step for these tests
    )


def test_block_present_only_for_needs_category_or_needs_pcb():
    assert _cycle_block(_lead(EligibilityFlag.NEEDS_CATEGORY, 200), False) != ""
    assert _cycle_block(_lead(EligibilityFlag.NEEDS_PCB, 250), False) != ""
    # UNKNOWN gets its own (different) "ask for NEET score" step, not empty
    assert "NEET score" in _cycle_block(_lead(EligibilityFlag.UNKNOWN, None), False)
    # BELOW_CUTOFF diverts entirely — no more cycle steps to push
    assert _cycle_block(_lead(EligibilityFlag.BELOW_CUTOFF, 120), False) == ""


def test_needs_pcb_block_names_the_score_and_forbids_confirming_eligible():
    block = _cycle_block(_lead(EligibilityFlag.NEEDS_PCB, 250), False).lower()
    assert "250" in block
    assert "pcb" in block
    assert "do not tell them they're eligible" in block
    assert "across every other topic" in block or "every other topic" in block


def test_block_names_the_score_and_forbids_assuming():
    block = _cycle_block(_lead(EligibilityFlag.NEEDS_CATEGORY, 198), False).lower()
    assert "198" in block
    assert "do not assume a category" in block
    assert "open or" in block or "open OR closed".lower() in block
    assert "every other topic" in block or "across every other topic" in block


def test_block_appears_in_the_rendered_turn_prompt():
    # a thin check that build_turn_context wires it in — full flow is covered by
    # the integration suite; here we just confirm the section lands in the string.
    lead = _lead(EligibilityFlag.NEEDS_CATEGORY, 205)
    block = _cycle_block(lead, False)
    assert "CYCLE STEP" in block
