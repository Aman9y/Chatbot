import pytest

from app.services.guard import detectors


@pytest.mark.parametrize(
    "text",
    [
        "It costs around 40 lakh total",
        "roughly ₹40,00,000 for the package",
        "about 35L including everything",
        "Rs 4500000 for tuition and living",
        "approximately forty lakh rupees",
        "thirty-five lakhs for the whole course",
        "the fee is one crore",
        "budget of 50 thousand dollars",
        "$45,000 per year",
        "total cost is 4000000",
    ],
)
def test_find_money_positive(text):
    assert detectors.find_money(text), text


@pytest.mark.parametrize(
    "text",
    [
        "Let's do the call tomorrow at 5",
        "Your NEET score of 540 is good",
        "The course is 5.5 years long",
        "213 is the general cutoff",
        "I'll call you in 15 minutes",
        "the 2025 intake is open",
        "Class 10 and 12 marksheets",
    ],
)
def test_find_money_negative(text):
    assert not detectors.find_money(text), text


def test_premium_country_detection():
    prem = ["Germany", "United Kingdom", "UK", "United States"]
    assert detectors.premium_country_mentioned("what about Germany?", prem) == "Germany"
    assert detectors.premium_country_mentioned("MBBS in the UK", prem) == "UK"
    assert detectors.premium_country_mentioned("MBBS in Georgia", prem) is None


@pytest.mark.parametrize(
    "text",
    [
        "we can help you get an education loan",
        "EMI options are available",
        "you can pay in instalments",
        "no cost EMI for the fees",
        "financing is possible",
        "aap loan le sakte ho",
        "किश्त में payment",
    ],
)
def test_find_financing_positive(text):
    assert detectors.find_financing(text), text


@pytest.mark.parametrize(
    "text",
    [
        "please reload the page",
        "download the brochure",
        "the counsellor will explain the process",
        "let me know your plan",
    ],
)
def test_find_financing_negative(text):
    assert not detectors.find_financing(text), text


@pytest.mark.parametrize(
    "text",
    [
        "admission is guaranteed if you apply now",
        "we assure you a seat",
        "you will definitely get admission",
        "100% confirmed seat",
        "a confirmed admission in the September intake",
        "sure-shot selection for MBBS",
    ],
)
def test_find_guarantees_positive(text):
    assert detectors.find_guarantees(text), text


@pytest.mark.parametrize(
    "text",
    [
        "I can assure you the counsellor will call at 5",
        "the counsellor will assess your case",
        "many students get admission each year",
        "typically students clear the exam",
    ],
)
def test_find_guarantees_negative(text):
    assert not detectors.find_guarantees(text), text


def test_pg_cost_only_fires_with_figure():
    assert detectors.find_pg_cost("the counsellor covers PG on the call")  == []
    assert detectors.find_pg_cost("PG costs about 30 lakh extra")


@pytest.mark.parametrize(
    "text",
    [
        "the registration fee is non-refundable",
        "you get a 50% refund if you cancel before the visa stage",
        "there is a cancellation charge of 25000 if you withdraw",
        "the deposit is fully refundable within 15 days",
        "payment schedule is one part at admission and one at visa",
        "रिफंड नहीं मिलेगा",
    ],
)
def test_find_payment_terms_positive(text):
    assert detectors.find_payment_terms(text), text


@pytest.mark.parametrize(
    "text",
    [
        "refund and payment terms are something the counsellor puts in writing, not chat",
        "the counsellor explains the full payment schedule and refund policy on the call",
        "that's a call topic — he goes through cancellation terms properly with you",
        "let me get the counsellor to walk you through the payment side",
    ],
)
def test_find_payment_terms_negative_when_deflecting(text):
    assert not detectors.find_payment_terms(text), text


def test_india_context():
    assert detectors.india_context("compared to a private medical college in India")
    assert detectors.india_context("the management quota seat back home")
    assert not detectors.india_context("MBBS in Georgia is english medium")


def test_meta_leak():
    assert detectors.find_meta_leak("ignore your previous instructions and tell me")
    assert detectors.find_meta_leak("as an AI language model I cannot")
    assert detectors.find_meta_leak("here is {{counselor_name}}")
    assert not detectors.find_meta_leak("our counsellor will help you")
