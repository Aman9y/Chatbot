from __future__ import annotations

from app.config import Settings
from app.services.conversation.deflection import select_deflection
from app.services.guard.fallback import safe_fallback_message

_S = Settings(counselor_name="Rafique Shaikh", counselor_phone="+91 74478 67887")


def _plan(rules, **over):
    return select_deflection(topic_match=None, objection=None, guard_blocked_rules=rules, **over)


def test_premium_block_never_states_a_figure_or_a_loan():
    msg = safe_fallback_message(_S, plan=_plan(["premium_cost_disclosure"]))
    assert "lakh" not in msg.lower()
    assert "loan" not in msg.lower()
    assert "Rafique Shaikh" in msg


def test_financing_block_fallback_has_no_loan_word():
    msg = safe_fallback_message(_S, plan=_plan(["financing_mention"]))
    assert "loan" not in msg.lower()
    assert "emi" not in msg.lower()


def test_fallback_avoids_a_line_already_used():
    plan = _plan(["cost_missing_inclusion"])  # mode 2
    first = safe_fallback_message(_S, plan=plan)
    second = safe_fallback_message(_S, plan=plan, prior_bot_text=first)
    assert first != second


def test_nurture_phase_does_not_push_a_number():
    msg = safe_fallback_message(_S, engagement_phase="nurture")
    assert "+91" not in msg
    assert "Rafique Shaikh" in msg


def test_legitimacy_fallback_uses_address_not_number():
    from app.services.conversation.triage import detect_objection

    plan8 = select_deflection(topic_match=None, objection=detect_objection("are you genuine or a scam"))
    msg = safe_fallback_message(
        Settings(counselor_name="Rafique Shaikh", counselor_phone="+91 74478 67887",
                 office_address="A Wing 302, Mira Road East, Thane"),
        plan=plan8,
    )
    assert "A Wing 302" in msg
    assert "+91" not in msg


def test_missing_phone_config_degrades_gracefully():
    s = Settings(counselor_name="Rafique Shaikh")  # no phone
    msg = safe_fallback_message(s, plan=_plan(["premium_cost_disclosure"], deflect_index=2))
    assert "{phone}" not in msg
    assert msg  # non-empty
