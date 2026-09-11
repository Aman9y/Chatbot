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
    # constant chat: soft at 4, direct at 6
    assert plan_pace(message_depth=2, minutes_since_last_bot=1, engagement_phase="push").cta_mode == "none"
    assert plan_pace(message_depth=4, minutes_since_last_bot=1, engagement_phase="push").cta_mode == "soft"
    assert plan_pace(message_depth=7, minutes_since_last_bot=1, engagement_phase="push").cta_mode == "direct"
    # moderate chat is slower to the direct ask
    assert plan_pace(message_depth=7, minutes_since_last_bot=40, engagement_phase="push").cta_mode == "soft"


def test_curious_host_stage_never_carries_a_cta():
    # depths 1-3 are curious-host; no CTA regardless of pace / chat speed
    for depth in (1, 2, 3):
        for mins in (1, 40, 300):
            p = plan_pace(message_depth=depth, minutes_since_last_bot=mins, engagement_phase="push")
            assert p.tone_stage == "curious_host"
            assert p.cta_mode == "none", (depth, mins, p.cta_mode)


def test_hour_override_forces_handoff_and_nurture():
    p = plan_pace(message_depth=4, minutes_since_last_bot=1, engagement_phase="handoff")
    assert p.tone_stage == "honest_handoff"
    assert p.cta_mode == "honest_handoff"
    n = plan_pace(message_depth=4, minutes_since_last_bot=1, engagement_phase="nurture")
    assert n.cta_mode == "nurture_soft"


# --- director review: close within roughly 7-8 exchanges -------------------
def test_moderate_pace_now_closes_by_message_8_not_10():
    # the old (5, 10) moderate ceiling dragged the direct ask out past the
    # 7-8-exchange target; it's now (4, 8).
    p = plan_pace(message_depth=8, minutes_since_last_bot=40, engagement_phase="push")
    assert p.cta_mode == "direct"


def test_every_pace_reaches_direct_by_message_8_at_the_latest():
    for pace_minutes in (1, 40, 300):  # constant, moderate, slow
        p = plan_pace(message_depth=8, minutes_since_last_bot=pace_minutes, engagement_phase="push")
        assert p.cta_mode == "direct", pace_minutes


# --- director review: engagement signals accelerate the CTA, not a fixed count --
def test_two_substantive_exchanges_accelerate_straight_to_direct():
    # depth 5 would normally still be "soft" on a moderate pace (ceiling 8) —
    # two genuinely substantive exchanges skip straight to "direct".
    quiet = plan_pace(
        message_depth=5, minutes_since_last_bot=40, engagement_phase="push", substantive_depth=0
    )
    assert quiet.cta_mode == "soft"
    engaged = plan_pace(
        message_depth=5, minutes_since_last_bot=40, engagement_phase="push", substantive_depth=2
    )
    assert engaged.cta_mode == "direct"


def test_engagement_signals_still_never_break_the_depth_1_to_3_floor():
    # even with heavy engagement, the first three messages stay CTA-free.
    for depth in (1, 2, 3):
        p = plan_pace(
            message_depth=depth, minutes_since_last_bot=1, engagement_phase="push",
            substantive_depth=3,
        )
        assert p.cta_mode == "none", depth


def test_engagement_earns_at_least_a_soft_mention_before_the_ceiling():
    p = plan_pace(
        message_depth=4, minutes_since_last_bot=40, engagement_phase="push", substantive_depth=2
    )
    assert p.cta_mode in ("soft", "direct")  # never "none" once genuinely engaged
