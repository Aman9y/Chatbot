import pytest

from app.errors import PhoneNormalizationError
from app.services.phone import normalize_phone, normalize_wa_id


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+919812345670", "+919812345670"),
        ("9812345670", "+919812345670"),
        ("09812345670", "+919812345670"),
        ("919812345670", "+919812345670"),
        ("98123 45670", "+919812345670"),
        ("98123-45670", "+919812345670"),
        ("+91 (98123) 45670", "+919812345670"),
        ("0091 9812345670", "+919812345670"),
    ],
)
def test_normalizes_indian_formats(raw, expected):
    assert normalize_phone(raw, "IN").e164 == expected


def test_keeps_country():
    result = normalize_phone("+14155552671", "IN")
    assert result.e164 == "+14155552671"
    assert result.country == "US"


@pytest.mark.parametrize("raw", ["", "   ", None, "12345", "abcde", "+1", "99999"])
def test_rejects_invalid(raw):
    with pytest.raises(PhoneNormalizationError):
        normalize_phone(raw, "IN")


def test_wa_id_without_plus():
    assert normalize_wa_id("919812345670", "IN").e164 == "+919812345670"


def test_raw_is_preserved():
    assert normalize_phone("  98123-45670 ", "IN").raw == "98123-45670"
