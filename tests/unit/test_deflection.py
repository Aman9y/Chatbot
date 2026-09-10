from __future__ import annotations

from app.models.enums import RoleHint
from app.services.conversation.deflection import (
    MODES,
    modes_reference,
    select_deflection,
    turn_hint,
)
from app.services.conversation.triage import classify_topic, detect_objection


def _topic(text):
    return classify_topic(text)


def _obj(text):
    return detect_objection(text)


# --- mode selection by reason -------------------------------------------
def test_full_answerable_topic_is_not_a_deflect():
    assert select_deflection(topic_match=_topic("is MBBS abroad recognised by NMC"), objection=None) is None


def test_premium_cost_is_mode_12_and_permanent():
    p = select_deflection(topic_match=_topic("how much does germany cost"), objection=None)
    assert p is not None and p.mode.num == 12 and p.mode.permanent


def test_pg_cost_is_mode_12():
    p = select_deflection(topic_match=_topic("what will PG cost me later"), objection=None)
    assert p.mode.num == 12


def test_financing_is_money_mode_2():
    p = select_deflection(topic_match=_topic("can I get an education loan"), objection=None)
    assert p.mode.num == 2


def test_profile_assessment_is_mode_3():
    p = select_deflection(topic_match=_topic("which university should I pick"), objection=None)
    assert p.mode.num == 3


def test_legitimacy_objection_is_mode_8_address():
    p = select_deflection(topic_match=None, objection=_obj("are you genuine or a scam"))
    assert p.mode.num == 8
    assert p.contact == "address"


def test_comparison_shopping_is_mode_10():
    p = select_deflection(topic_match=None, objection=_obj("another agent offered it cheaper"))
    assert p.mode.num == 10


def test_stall_brochure_is_mode_11():
    p = select_deflection(topic_match=None, objection=_obj("just send me the brochure"))
    assert p.mode.num == 11


def test_anxious_parent_is_mode_9():
    p = select_deflection(
        topic_match=_topic("is it safe there for my daughter"),
        objection=None,
        speaker=RoleHint.PARENT,
    )
    assert p.mode.num == 9


# --- escalation / repeat ------------------------------------------------
def test_repeat_forces_mode_6():
    p = select_deflection(
        topic_match=_topic("which university should I pick"),
        objection=None,
        repeat_detected=True,
    )
    assert p.mode.num == 6
    assert p.escalated_from == 3


def test_same_mode_twice_becomes_mode_6():
    p = select_deflection(
        topic_match=_topic("which university should I pick"),
        objection=None,
        last_mode=3,
    )
    assert p.mode.num == 6


def test_third_deflect_escalates_to_6_or_7():
    calm = select_deflection(
        topic_match=_topic("which university should I pick"), objection=None, deflect_index=2
    )
    assert calm.mode.num == 6
    hot = select_deflection(
        topic_match=_topic("which university should I pick"),
        objection=None,
        deflect_index=2,
        frustrated=True,
    )
    assert hot.mode.num == 7


def test_permanent_block_does_not_escalate_away():
    p = select_deflection(
        topic_match=_topic("how much does germany cost"),
        objection=None,
        deflect_index=3,
        repeat_detected=True,
    )
    assert p.mode.num == 12


# --- contact-detail rules ---------------------------------------------
def test_first_deflect_carries_no_contact():
    p = select_deflection(topic_match=_topic("which university should I pick"), objection=None, deflect_index=0)
    assert p.contact == "none"


def test_second_deflect_carries_the_number():
    p = select_deflection(topic_match=_topic("can I get a loan"), objection=None, deflect_index=1)
    assert p.contact == "number"


def test_modes_8_and_9_use_address_not_number():
    p = select_deflection(topic_match=None, objection=_obj("are you genuine or a scam"))
    assert p.contact in ("address", "address_brief")
    q = select_deflection(
        topic_match=_topic("is it safe there for my daughter"),
        objection=None,
        speaker=RoleHint.PARENT,
    )
    assert q.contact in ("address", "address_brief")


# --- guard-fallback path --------------------------------------------
def test_guard_rules_map_to_modes():
    assert select_deflection(topic_match=None, objection=None, guard_blocked_rules=["premium_cost_disclosure"]).mode.num == 12
    assert select_deflection(topic_match=None, objection=None, guard_blocked_rules=["financing_mention"]).mode.num == 2
    assert select_deflection(topic_match=None, objection=None, guard_blocked_rules=["multi_country_cost"]).mode.num == 3
    # unknown rule -> generic assessment mode, never a crash
    assert select_deflection(topic_match=None, objection=None, guard_blocked_rules=["something_new"]).mode.num == 3


# --- rendering -------------------------------------------------------
def test_modes_reference_lists_all_13():
    ref = modes_reference()
    for n in range(1, 14):
        assert f"{n}. " in ref
    assert "never the number and the address in the same message" in ref.lower()
    assert "mode 12 is permanent" in ref.lower()


def test_turn_hint_fills_contact_and_register():
    p = select_deflection(topic_match=None, objection=_obj("are you genuine or a scam"))
    hint = turn_hint(p, counselor_phone="+91 74478 67887", office_address="A Wing 302, Mira Road")
    assert "mode: 8" in hint
    assert "A Wing 302" in hint
    assert "Not the number" in hint


def test_turn_hint_number_mode_names_the_phone():
    p = select_deflection(topic_match=_topic("can I get a loan"), objection=None, deflect_index=1)
    hint = turn_hint(p, counselor_phone="+91 74478 67887")
    assert "+91 74478 67887" in hint
    assert "Not the address" in hint


def test_every_mode_has_a_register_and_when():
    for m in MODES.values():
        assert m.register and m.when and m.title


def test_no_register_calls_the_bot_an_assistant_or_promises_a_callback():
    from app.services.conversation.deflection import modes_reference

    banned = (
        "i'm the assistant", "i'm a basic assistant", "i am an assistant",
        "he'll reach out", "he will reach out", "will contact you",
        "i'll set it up", "i'll set up", "can i set up", "can i arrange",
        "would you like me to arrange", "shall i set",
    )
    for m in MODES.values():
        low = m.register.lower()
        for b in banned:
            assert b not in low, (m.num, b)
    ref = modes_reference("Stellar AI").lower()
    assert "be honest you're the assistant" not in ref
    assert "lead reaching out to the director" in ref


def test_turn_hint_frames_number_as_the_lead_calling_him():
    p = select_deflection(topic_match=_topic("can I get a loan"), objection=None, deflect_index=1)
    hint = turn_hint(p, counselor_name="Rafique Shaikh", counselor_phone="+91 74478 67887")
    low = hint.lower()
    assert "for them to call or message him" in low
    assert "never as him contacting them" in low
