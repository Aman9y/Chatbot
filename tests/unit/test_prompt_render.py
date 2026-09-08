from app.config import Settings
from app.services.conversation.prompt import render_system_prompt


def test_unset_placeholders_render_generic():
    text = render_system_prompt(Settings(company_name="", counselor_name=""))
    assert "{{" not in text
    assert "our team" in text
    assert "our counsellor" in text


def test_configured_values_appear():
    text = render_system_prompt(
        Settings(company_name="MedPath", counselor_name="Dr. Rao", office_address="12 MG Road")
    )
    assert "MedPath" in text
    assert "Dr. Rao" in text
    assert "12 MG Road" in text


def test_cost_clause_when_range_unset():
    text = render_system_prompt(Settings(stateable_cost_range=None))
    assert "do NOT state a specific figure" in text.lower() or "do not state a specific figure" in text.lower()


def test_cost_clause_when_range_set():
    text = render_system_prompt(Settings(stateable_cost_range="30-35 lakh"))
    assert "30-35 lakh" in text


def test_neet_clause_hidden_when_cutoffs_unset():
    text = render_system_prompt(Settings(neet_cutoff_general=None, neet_cutoff_obc=None))
    assert "do not state a specific neet cutoff" in text.lower()


def test_neet_clause_shown_when_configured():
    text = render_system_prompt(
        Settings(neet_cutoff_general=213, neet_cutoff_obc=175, neet_year=2025)
    )
    assert "213" in text and "175" in text


def test_defaults_now_carry_confirmed_values():
    text = render_system_prompt(Settings())
    assert "213" in text and "175" in text and "2026" in text
    assert "₹30–35 lakh" in text
    assert "₹80L–1.2Cr" in text


def test_india_compare_clause_qualitative_when_unset():
    text = render_system_prompt(Settings(india_compare_cost_range=None))
    assert "several times more" in text.lower()
