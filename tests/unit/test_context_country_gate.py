"""The country-discussed gate note is driven by tracked lead state."""

from app.models.lead import Lead
from app.services.conversation.context import _country_gate_block


def _lead(discussed: bool) -> Lead:
    return Lead(phone_e164="+919812345670", country_discussed=discussed)


def test_block_present_only_when_not_yet_discussed():
    assert _country_gate_block(_lead(False)) != ""
    assert _country_gate_block(_lead(True)) == ""


def test_block_names_the_enforced_gate():
    block = _country_gate_block(_lead(False)).lower()
    assert "call, number, or office" in block or "call, a meeting" in block or "gate" in block
    assert "country" in block
