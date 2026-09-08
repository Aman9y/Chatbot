from __future__ import annotations

import pytest

from app.services.conversation.triage import classify_topic, detect_objection


@pytest.mark.parametrize(
    "text,topic_id,handling",
    [
        ("can I get an education loan for the fees?", "financing", "HARD_DEFLECT"),
        ("do you offer any scholarship?", "scholarship", "HARD_DEFLECT"),
        ("what's the refund policy if I cancel?", "payment_refund", "HARD_DEFLECT"),
        ("can you guarantee my admission?", "guarantee", "HARD_DEFLECT"),
        ("how much will PG cost later", "pg_cost", "HARD_DEFLECT"),
        ("which university should I choose?", "university_pick", "SOFT_DEFLECT"),
        ("how much are the fees roughly", "fees_general", "PARTIAL"),
        ("is MBBS abroad recognised by NMC?", "nmc_recognition", "FULL"),
        ("how hard is the FMGE exam", "fmge_next", "FULL"),
        ("is it safe there for girls", "safety", "FULL"),
        ("how do I pay the fee and start documents now", "high_intent", "FULL"),
    ],
)
def test_classify_topic(text, topic_id, handling):
    m = classify_topic(text)
    assert m is not None
    assert m.rule.id == topic_id
    assert m.handling == handling


def test_combo_surfaces_hard_deflect_secondary():
    m = classify_topic("what are the fees, and can I get a loan or scholarship?")
    assert m is not None
    hard_ids = {r.id for r in m.hard_deflect_topics}
    assert {"financing", "scholarship"} & hard_ids
    # dominant intent is still fees
    assert m.rule.id in ("fees_general", "financing", "scholarship")


def test_high_intent_flag():
    m = classify_topic("I'm ready to proceed, how do we start the process now?")
    assert m is not None and m.high_intent


def test_no_match_returns_none():
    assert classify_topic("good morning") is None


@pytest.mark.parametrize(
    "text,obj_id",
    [
        ("let me think about it", "think_about_it"),
        ("I need to talk to my parents first", "talk_to_family"),
        ("how do I know you are legit", "legit"),
        ("my friend's consultant is offering it cheaper", "cheaper_elsewhere"),
        ("I'm just researching right now", "just_researching"),
        ("what if we get scammed", "scam_fear"),
        ("just send me the brochure and university list", "send_everything"),
        ("just tell me on whatsapp", "just_tell_me_here"),
        ("honestly not interested", "not_interested"),
    ],
)
def test_detect_objection(text, obj_id):
    o = detect_objection(text)
    assert o is not None and o.id == obj_id


def test_no_objection():
    assert detect_objection("tell me about Georgia") is None
