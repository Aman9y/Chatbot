from __future__ import annotations

import pytest

from app.services.conversation.pacing import classify_pace, plan_pace


@pytest.mark.parametrize(
    "minutes,expected",
    [(None, "moderate"), (2.0, "constant"), (40.0, "moderate"), (300.0, "slow")],
)
def test_classify_pace(minutes, expected):
    assert classify_pace(minutes) == expected


def test_tone_stage_progression():
    assert plan_pace(message_depth=1, minutes_since_last_bot=2, engagement_phase="push").tone_stage == "curious_host"
    assert plan_pace(message_depth=6, minutes_since_last_bot=2, engagement_phase="push").tone_stage == "helpful_expert"
    assert plan_pace(message_depth=12, minutes_since_last_bot=2, engagement_phase="push").tone_stage == "bridge_builder"
    assert plan_pace(message_depth=18, minutes_since_last_bot=2, engagement_phase="push").tone_stage == "honest_handoff"


def test_cta_mode_by_pace_and_depth():
    # constant chat: soft at 3, direct at 6
    assert plan_pace(message_depth=2, minutes_since_last_bot=1, engagement_phase="push").cta_mode == "none"
    assert plan_pace(message_depth=4, minutes_since_last_bot=1, engagement_phase="push").cta_mode == "soft"
    assert plan_pace(message_depth=7, minutes_since_last_bot=1, engagement_phase="push").cta_mode == "direct"
    # moderate chat is slower to the direct ask
    assert plan_pace(message_depth=7, minutes_since_last_bot=40, engagement_phase="push").cta_mode == "soft"


def test_hour_override_forces_handoff_and_nurture():
    p = plan_pace(message_depth=4, minutes_since_last_bot=1, engagement_phase="handoff")
    assert p.tone_stage == "honest_handoff"
    assert p.cta_mode == "honest_handoff"
    n = plan_pace(message_depth=4, minutes_since_last_bot=1, engagement_phase="nurture")
    assert n.cta_mode == "nurture_soft"
