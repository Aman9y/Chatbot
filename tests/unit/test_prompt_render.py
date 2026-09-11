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


def test_bot_identity_is_stellar_ai_female_not_the_assistant():
    text = render_system_prompt(Settings())
    low = " ".join(text.lower().split())  # collapse markdown line wraps
    assert "you are stellar ai" in low
    assert 'as "she"' in low
    assert "i am the automated assistant for" in low  # listed as a phrasing to avoid
    assert "you are just stellar ai" in low
    # a custom bot_name flows everywhere, including the deflection reference
    t2 = render_system_prompt(Settings(bot_name="Acme Bot", bot_pronoun_subject="they"))
    assert "You are Acme Bot" in t2
    assert "Stellar AI" not in t2


def test_contact_direction_lead_reaches_out_never_the_reverse():
    text = render_system_prompt(Settings())
    low = " ".join(text.lower().split())
    assert "the lead reaches out to" in low
    assert 'never say he "will call you"' in low
    assert "the next move is theirs" in low
    # no lingering "counsellor will reach out" close mechanic
    assert "will reach out to set the time" not in low
    assert "will take it from here" not in low


def test_hindi_rules_carry_the_identity_and_contact_constraints():
    text = render_system_prompt(Settings())
    assert "Every rule in this prompt holds in every language" in text
    assert "मैं Stellar AI" in text
    assert "call कर सकते ह" in text  # them calling him, in the Hindi example


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


def test_deflection_modes_section_is_rendered():
    text = render_system_prompt(Settings())
    # all 13 modes present, plus the two hard rules
    for n in range(1, 14):
        assert f"\n{n}. " in text
    assert "never the number and the address in the same message" in text.lower()
    assert "mode 12 is permanent" in text.lower()


def test_prompt_gates_cost_and_pitch_on_openers():
    text = render_system_prompt(Settings()).lower()
    # cost figures are answer-only, never volunteered
    assert "answer-only" in text or "never volunteer a number" in text
    assert "do not mention any figure" in text or "not mention any figure" in text
    # small-talk carve-out with no pitch
    assert "small talk" in text
    assert "sales opportunity" in text
    assert "no mention of a call" in text
    # CTA guidance overrides the general reply shape
    assert "per-turn cta guidance" in text
    assert "override" in text


def test_deflection_reference_scopes_itself_to_actual_deflections():
    text = render_system_prompt(Settings()).lower()
    assert "a greeting, small talk, or a question you can simply answer is not a deflection" in text


# --- director review: parent-join prompts deactivated behind a config flag --
def test_parent_join_prompts_absent_by_default():
    text = render_system_prompt(Settings())
    assert "{{" not in text
    assert "Offer to include a parent" not in text
    assert "Offer to have them on the call with their child" not in text


def test_parent_join_prompts_present_when_flag_enabled():
    text = render_system_prompt(Settings(parent_prompt_enabled=True))
    assert "{{" not in text
    assert "Offer to include a parent" in text
    assert "Offer to have them on the call with their child" in text


# --- director review: PCB is a second, equally-required eligibility axis ---
def test_needs_pcb_branch_present_in_eligibility_section():
    text = render_system_prompt(Settings()).lower()
    assert "needs_pcb" in text
    assert "pcb" in text and "physics" in text and "chemistry" in text and "biology" in text
    assert "50%" in text and "45%" in text


# --- director review: tone rules -------------------------------------------
def test_tone_rules_praise_score_and_confident_language():
    text = render_system_prompt(Settings())
    low = " ".join(text.lower().split())
    assert "that's a good score" in low or "solid score" in low
    assert "i can help you narrow down a country" in low
    assert "simple in both english and hindi" in low


# --- director review: university list closing line -------------------------
def test_university_closing_line_standing_rule_present():
    text = render_system_prompt(Settings())
    assert "any specific college you have in mind" in text


# --- director review: abroad-average framing, Russia/Georgia deferred ------
def test_cost_clause_names_abroad_average_trio_and_defers_sensitive_countries():
    text = render_system_prompt(Settings())
    low = text.lower()
    assert "abroad average" in low
    assert "uzbekistan, kazakhstan and kyrgyzstan" in low
    assert "own separate pricing" in low
