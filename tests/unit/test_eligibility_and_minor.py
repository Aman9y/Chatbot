from datetime import date

from app.config import Settings
from app.models.enums import EligibilityFlag, MinorStatus, NeetCategory
from app.services.consent import evaluate_minor, years_between
from app.services.eligibility import compute_eligibility


def test_eligibility_unknown_when_cutoffs_not_configured():
    s = Settings(neet_cutoff_general=None, neet_cutoff_obc=None)
    assert compute_eligibility(650, NeetCategory.GENERAL, s) == EligibilityFlag.UNKNOWN


def test_eligibility_with_configured_cutoffs():
    s = Settings(neet_cutoff_general=213, neet_cutoff_obc=175)
    assert compute_eligibility(250, NeetCategory.GENERAL, s) == EligibilityFlag.ABOVE_CUTOFF
    assert compute_eligibility(200, NeetCategory.GENERAL, s) == EligibilityFlag.BELOW_CUTOFF
    assert compute_eligibility(180, NeetCategory.OBC, s) == EligibilityFlag.ABOVE_CUTOFF
    assert compute_eligibility(150, NeetCategory.OBC, s) == EligibilityFlag.BELOW_CUTOFF
    assert compute_eligibility(None, NeetCategory.GENERAL, s) == EligibilityFlag.UNKNOWN


def test_eligibility_ambiguous_band_needs_category():
    s = Settings(neet_cutoff_general=213, neet_cutoff_obc=175)
    U = NeetCategory.UNKNOWN
    # inside the band (175..212) with no category -> genuinely undetermined
    assert compute_eligibility(200, U, s) == EligibilityFlag.NEEDS_CATEGORY
    assert compute_eligibility(175, U, s) == EligibilityFlag.NEEDS_CATEGORY
    assert compute_eligibility(212, U, s) == EligibilityFlag.NEEDS_CATEGORY
    # above the higher bound / below the lower bound -> category can't change it
    assert compute_eligibility(213, U, s) == EligibilityFlag.ABOVE_CUTOFF
    assert compute_eligibility(174, U, s) == EligibilityFlag.BELOW_CUTOFF
    # category supplied -> resolves either way
    assert compute_eligibility(200, NeetCategory.RESERVED, s) == EligibilityFlag.ABOVE_CUTOFF
    assert compute_eligibility(200, NeetCategory.GENERAL, s) == EligibilityFlag.BELOW_CUTOFF
    # None category behaves like UNKNOWN
    assert compute_eligibility(200, None, s) == EligibilityFlag.NEEDS_CATEGORY
    # no cutoffs configured -> still UNKNOWN, never NEEDS_CATEGORY
    assert compute_eligibility(200, U, Settings(neet_cutoff_general=None, neet_cutoff_obc=None)) == EligibilityFlag.UNKNOWN


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
