"""The country-discussed gate note is driven by tracked (guard-effective) state."""

from app.services.conversation.context import _country_gate_block


def test_block_present_only_when_not_yet_discussed():
    assert _country_gate_block(False) != ""
    assert _country_gate_block(True) == ""


def test_block_names_the_enforced_gate():
    block = _country_gate_block(False).lower()
    assert "call, number, or office" in block or "call, a meeting" in block or "gate" in block
    assert "country" in block
