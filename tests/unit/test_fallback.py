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


def test_nurture_phase_gives_the_number_for_the_lead_to_use():
    msg = safe_fallback_message(_S, engagement_phase="nurture")
    assert "Rafique Shaikh" in msg
    # no "he'll call you" framing
    assert "reach out to you" not in msg.lower()
    assert "will call" not in msg.lower()


def test_no_fallback_line_calls_the_bot_an_assistant_or_promises_a_callback():
    from app.services.guard.fallback import _NURTURE, _POOLS

    banned = ("assistant", "he'll reach out", "he will reach out", "i'll set",
              "can i set", "can i arrange", "shall i set", "want me to arrange",
              "would you like me to arrange")
    for pool in list(_POOLS.values()) + [_NURTURE]:
        for line in pool:
            low = line.lower()
            for b in banned:
                assert b not in low, (b, line)


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


# --- language-aware fallback (round-4 caveat fix 2) --------------------
def test_is_hindi_detection():
    from app.services.guard.fallback import is_hindi

    assert is_hindi("kya aap guarantee dete ho admission ki")
    assert is_hindi("हम guarantee नहीं दे सकते")
    assert is_hindi("mera beta bahar padhega, kitna kharcha aayega")
    assert not is_hindi("will Rafique Sir call me back to explain the process")
    assert not is_hindi("How much does MBBS in Georgia cost, and is it NMC recognised")
    assert not is_hindi("ok so what is the next step then")


def test_hindi_conversation_gets_a_hindi_fallback():
    plan = _plan(["admission_guarantee"])  # -> mode 12
    convo = "kya aap guarantee dete ho admission ki, mera beta MBBS karna chahta hai"
    hi = safe_fallback_message(_S, plan=plan, conversation_text=convo)
    en = safe_fallback_message(_S, plan=plan, conversation_text="is admission guaranteed")
    assert "keh sakti" in hi or "samjha denge" in hi  # unmistakably Hindi
    assert hi != en
    assert "I'm not going to" not in hi
    assert "Rafique Shaikh" in hi


def test_hindi_fallback_never_leaves_a_stray_template_token():
    for rules in (["financing_mention"], ["admission_guarantee"], ["multi_country_cost"],
                  ["overpromise"], ["cost_missing_inclusion"]):
        plan = _plan(rules)
        msg = safe_fallback_message(
            _S, plan=plan, conversation_text="kya iske liye loan mil sakta hai humein bataiye"
        )
        assert "{" not in msg and "}" not in msg
        # no dangling postposition from a stripped {phone}
        assert " par call" not in msg or "+91" in msg
        assert "Shaikh par " not in msg


def test_hindi_nurture_fallback_is_hindi():
    msg = safe_fallback_message(
        _S, engagement_phase="nurture",
        conversation_text="theek hai main baad mein aur baat karungi aapse",
    )
    assert "Rafique Shaikh" in msg
    assert any(w in msg for w in ("Jab", "jaldi", "seedhe"))
