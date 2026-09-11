from datetime import date

from app.config import Settings
from app.models.enums import EligibilityFlag, MinorStatus, NeetCategory
from app.services.consent import evaluate_minor, years_between
from app.services.eligibility import compute_eligibility

# A PCB% that clears both the general (50) and OBC (45) default cutoffs, used
# wherever a test is only exercising the NEET-score axis and doesn't want the
# PCB axis (director review: an equally-required second axis) to interfere.
_PCB_CLEAR = 60.0


def test_eligibility_unknown_when_cutoffs_not_configured():
    s = Settings(neet_cutoff_general=None, neet_cutoff_obc=None)
    assert compute_eligibility(650, NeetCategory.GENERAL, _PCB_CLEAR, s) == EligibilityFlag.UNKNOWN


def test_eligibility_with_configured_cutoffs():
    s = Settings(neet_cutoff_general=213, neet_cutoff_obc=175)
    assert (
        compute_eligibility(250, NeetCategory.GENERAL, _PCB_CLEAR, s)
        == EligibilityFlag.ABOVE_CUTOFF
    )
    assert (
        compute_eligibility(200, NeetCategory.GENERAL, _PCB_CLEAR, s)
        == EligibilityFlag.BELOW_CUTOFF
    )
    assert compute_eligibility(180, NeetCategory.OBC, _PCB_CLEAR, s) == EligibilityFlag.ABOVE_CUTOFF
    assert compute_eligibility(150, NeetCategory.OBC, _PCB_CLEAR, s) == EligibilityFlag.BELOW_CUTOFF
    assert compute_eligibility(None, NeetCategory.GENERAL, _PCB_CLEAR, s) == EligibilityFlag.UNKNOWN


def test_eligibility_ambiguous_band_needs_category():
    s = Settings(neet_cutoff_general=213, neet_cutoff_obc=175)
    U = NeetCategory.UNKNOWN
    # inside the band (175..212) with no category -> genuinely undetermined
    assert compute_eligibility(200, U, _PCB_CLEAR, s) == EligibilityFlag.NEEDS_CATEGORY
    assert compute_eligibility(175, U, _PCB_CLEAR, s) == EligibilityFlag.NEEDS_CATEGORY
    assert compute_eligibility(212, U, _PCB_CLEAR, s) == EligibilityFlag.NEEDS_CATEGORY
    # above the higher bound / below the lower bound -> category can't change it
    assert compute_eligibility(213, U, _PCB_CLEAR, s) == EligibilityFlag.ABOVE_CUTOFF
    assert compute_eligibility(174, U, _PCB_CLEAR, s) == EligibilityFlag.BELOW_CUTOFF
    # category supplied -> resolves either way
    assert (
        compute_eligibility(200, NeetCategory.RESERVED, _PCB_CLEAR, s)
        == EligibilityFlag.ABOVE_CUTOFF
    )
    assert (
        compute_eligibility(200, NeetCategory.GENERAL, _PCB_CLEAR, s)
        == EligibilityFlag.BELOW_CUTOFF
    )
    # None category behaves like UNKNOWN
    assert compute_eligibility(200, None, _PCB_CLEAR, s) == EligibilityFlag.NEEDS_CATEGORY
    # no cutoffs configured -> still UNKNOWN, never NEEDS_CATEGORY
    assert (
        compute_eligibility(
            200, U, _PCB_CLEAR, Settings(neet_cutoff_general=None, neet_cutoff_obc=None)
        )
        == EligibilityFlag.UNKNOWN
    )


# --- director review: PCB% is a second, equally-required eligibility axis --
def test_pcb_axis_required_alongside_neet_score():
    s = Settings(neet_cutoff_general=213, neet_cutoff_obc=175)
    G = NeetCategory.GENERAL
    # NEET clears but PCB% not stated yet -> NEEDS_PCB, not eligible yet.
    assert compute_eligibility(250, G, None, s) == EligibilityFlag.NEEDS_PCB
    # NEET clears, PCB% stated and clears (>= 50 general) -> eligible.
    assert compute_eligibility(250, G, 55.0, s) == EligibilityFlag.ABOVE_CUTOFF
    # NEET clears, PCB% stated but fails (< 50 general) -> not eligible, full
    # stop, regardless of how well they did on the NEET score.
    assert compute_eligibility(250, G, 40.0, s) == EligibilityFlag.BELOW_CUTOFF
    # NEET fails, PCB% clears -> still not eligible (either axis failing is
    # conclusive).
    assert compute_eligibility(150, G, 90.0, s) == EligibilityFlag.BELOW_CUTOFF
    # both fail -> not eligible.
    assert compute_eligibility(150, G, 20.0, s) == EligibilityFlag.BELOW_CUTOFF
    # OBC PCB cutoff is 45, not 50.
    obc = NeetCategory.OBC
    assert compute_eligibility(180, obc, 46.0, s) == EligibilityFlag.ABOVE_CUTOFF
    assert compute_eligibility(180, obc, 44.0, s) == EligibilityFlag.BELOW_CUTOFF


def test_pcb_ambiguous_band_needs_category_before_pcb():
    s = Settings(neet_cutoff_general=213, neet_cutoff_obc=175)
    U = NeetCategory.UNKNOWN
    # NEET clears regardless of category (>=213), but PCB% sits in the
    # general/OBC ambiguous band (45..49) -> category decides the PCB axis too.
    assert compute_eligibility(250, U, 47.0, s) == EligibilityFlag.NEEDS_CATEGORY


def test_years_between():
    assert years_between(date(2006, 6, 15), date(2024, 6, 14)) == 17
    assert years_between(date(2006, 6, 15), date(2024, 6, 15)) == 18


def test_evaluate_minor():
    assert evaluate_minor(None, None)[0] == MinorStatus.UNKNOWN
    assert evaluate_minor(None, 17)[0] == MinorStatus.YES
    assert evaluate_minor(None, 19)[0] == MinorStatus.NO
    status, age = evaluate_minor(date(2010, 1, 1), None, today=date(2024, 1, 1))
    assert status == MinorStatus.YES
    assert age == 14
