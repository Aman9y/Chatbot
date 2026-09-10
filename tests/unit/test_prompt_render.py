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


def test_cost_clause_when_no_ranges_configured():
    text = render_system_prompt(Settings(country_cost_ranges="{}"))
    assert "no cost ranges are configured" in text.lower()


def test_cost_clause_lists_each_country_with_its_own_range():
    text = render_system_prompt(Settings())
    assert "Uzbekistan: ₹30–35 lakh" in text
    assert "Russia: ₹27–45 lakh" in text
    assert "Bangladesh: ₹32–45 lakh" in text


def test_cost_clause_flags_georgia_and_nepal_and_offers_director_number():
    text = render_system_prompt(
        Settings(counselor_name="Rafique Shaikh", counselor_phone="+91 74478 67887")
    )
    assert "Georgia: ₹38–55 lakh — HIGHER-COST" in text
    assert "Nepal: ₹57–80 lakh — HIGHER-COST" in text
    # justification + director's number required in the same reply
    assert "almost identical to India" in text
    assert "different education market" in text
    assert "+91 74478 67887" in text


def test_about_clause_states_both_experience_and_registration():
    text = render_system_prompt(Settings(company_name="Stellar Educonsultancy"))
    assert "over ten years" in text
    assert "2024" in text
    assert "government" in text.lower()
    assert "parent company" in text.lower()  # instruction not to name one


def test_contact_clause_gives_number_on_explicit_ask_and_sensitive_costs():
    text = render_system_prompt(
        Settings(counselor_name="Rafique Shaikh", counselor_phone="+91 74478 67887")
    )
    assert "+91 74478 67887" in text
    assert "explicitly asks" in text.lower()


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
