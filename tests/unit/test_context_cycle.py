"""The qualification cycle (interest -> eligibility -> country -> Rafique
Sir) as an always-return-to spine (director review): exactly one CYCLE STEP
block is active per turn, driven entirely by tracked lead state. Eligibility
steps (NEET score / category / PCB) are covered in test_context_eligibility.py
— this file covers the rest: interest, country decision, and the Rafique Sir
introduction, plus the state-machine priority ordering and the "cycle
complete -> hand off" end state.
"""

from app.models.enums import EligibilityFlag
from app.models.lead import Lead
from app.services.conversation.context import _cycle_block


def _lead(**over) -> Lead:
    base = dict(
        phone_e164="+919812345670",
        considering_abroad=True,
        eligibility_flag=EligibilityFlag.ABOVE_CUTOFF,
        neet_score=250,
    )
    base.update(over)
    return Lead(**base)


# --- step: interest --------------------------------------------------------
def test_interest_step_active_when_unknown():
    block = _cycle_block(_lead(considering_abroad=None), False)
    assert "CYCLE STEP" in block
    assert "india vs abroad" in block.lower() or "abroad" in block.lower()


def test_india_only_lead_skips_the_rest_of_the_cycle_entirely():
    assert _cycle_block(_lead(considering_abroad=False), False) == ""
    assert _cycle_block(
        _lead(considering_abroad=False, eligibility_flag=EligibilityFlag.UNKNOWN), False
    ) == ""


def test_interest_step_not_reasked_once_answered():
    # considering_abroad=True with a resolved eligibility flag and a decided
    # country lands on the Rafique step, not back on interest.
    block = _cycle_block(_lead(target_country="Georgia", country_discussed=True), True)
    assert "interest" not in block.lower()


# --- step: country decision -------------------------------------------------
def test_country_decision_asked_once_when_neither_decided_nor_deciding():
    block = _cycle_block(_lead(), False)
    assert "country decision" in block.lower()


def test_country_decision_not_reasked_once_marked_still_deciding():
    block = _cycle_block(_lead(country_still_deciding=True), False)
    assert "country decision" not in block.lower()
    assert "still deciding" in block.lower()
    # exact required substance
    assert "bangladesh" in block.lower()
    assert "georgia" in block.lower()
    assert "russia" in block.lower()
    assert "uzbekistan" in block.lower()
    assert "kazakhstan" in block.lower()
    assert "kyrgyzstan" in block.lower()
    assert "male and female" in block.lower()
    assert "do not mention cost" in block.lower()


def test_country_decision_not_reasked_once_a_country_is_named():
    block = _cycle_block(_lead(target_country="Georgia"), False)
    assert "country decision" not in block.lower()
    assert "georgia" in block.lower()
    assert "any specific college you have in mind" in block.lower()


def test_country_content_stops_once_a_real_back_and_forth_happened():
    # effective_country_discussed=True means the content already landed —
    # don't keep re-rendering the college list / comparison every turn.
    block = _cycle_block(_lead(target_country="Georgia"), True)
    assert "cycle step — country" not in block.lower()


# --- step: Rafique Sir introduction -----------------------------------------
def test_rafique_intro_step_active_once_country_discussed():
    block = _cycle_block(_lead(target_country="Georgia"), True)
    assert "rafique sir" in block.lower()
    assert "10+ years" in block or "10+ years" in block.lower()
    assert "i'm just stellar ai" in block.lower()


def test_rafique_intro_never_appears_before_country_discussed():
    block = _cycle_block(_lead(target_country="Georgia"), False)
    assert "introduce rafique sir" not in block.lower()


def test_cycle_complete_hands_off_once_rafique_introduced():
    block = _cycle_block(
        _lead(target_country="Georgia", country_discussed=True, rafique_introduced=True),
        True,
    )
    assert block == ""


# --- below-cutoff diverts, never reaches the country/Rafique steps ---------
def test_below_cutoff_never_reaches_country_or_rafique_steps():
    block = _cycle_block(_lead(eligibility_flag=EligibilityFlag.BELOW_CUTOFF, neet_score=120), True)
    assert block == ""
