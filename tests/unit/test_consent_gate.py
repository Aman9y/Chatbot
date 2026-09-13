from __future__ import annotations

import time

import pytest

from app.services.conversation.consent_gate import (
    heuristic_age,
    heuristic_optin,
    interpret_age_reply,
    interpret_optin_reply,
    stated_age,
)
from tests.helpers import HangingLLMClient


@pytest.mark.parametrize(
    "text,expected",
    [
        ("yes", "yes"),
        ("yeah sure", "yes"),
        ("haan", "yes"),
        ("bilkul batao", "yes"),
        ("ok tell me more", "yes"),
        ("interested", "yes"),
        ("no", "no"),
        ("nahi", "no"),
        ("no thanks", "no"),
        ("not interested", "no"),
        ("nahi chahiye", "no"),
        ("who is this?", "unclear"),
        ("how did you get my number", "unclear"),
        ("maybe later", "unclear"),
        ("", "unclear"),
    ],
)
def test_heuristic_optin(text, expected):
    assert heuristic_optin(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("I'm 24", "adult"),
        ("22", "adult"),
        ("yes I'm over 18", "adult"),
        ("18+", "adult"),
        ("i am an adult", "adult"),
        ("haan 18 se upar", "adult"),
        ("17", "minor"),
        ("I'm 16 years old", "minor"),
        ("under 18", "minor"),
        ("not 18 yet", "minor"),
        ("abhi 17 saal", "minor"),
        ("yes", "unclear"),
        ("no", "unclear"),
        ("why do you ask", "unclear"),
        ("does it matter", "unclear"),
    ],
)
def test_heuristic_age(text, expected):
    assert heuristic_age(text) == expected


def test_stated_age():
    assert stated_age("I'm 17 years old") == 17
    assert stated_age("22") == 22
    assert stated_age("sometime around 2026") is None


async def test_interpreters_never_default_to_green_light():
    # fake provider -> no LLM; an unclear reply must stay unclear
    assert await interpret_optin_reply("what does this involve exactly") == "unclear"
    assert await interpret_age_reply("yes") == "unclear"


async def test_llm_refines_unclear(llm_client):
    from app.services.llm.fake import FakeLLMClient

    llm = FakeLLMClient(json_responses=[{"answer": "yes"}])
    assert (
        await interpret_optin_reply(
            "sounds like something worth knowing", llm=llm, model="x", use_llm=True
        )
        == "yes"
    )
    llm2 = FakeLLMClient(json_responses=[{"answer": "minor"}])
    assert (
        await interpret_age_reply("still in school", llm=llm2, model="x", use_llm=True)
        == "minor"
    )


async def test_gate_interpreters_time_out_instead_of_hanging():
    """Production incident (2026-09-12, task 9c1a6ae2): a Celery turn hung with
    no error at all — the consent/age gate's LLM refinement was one of the
    unwrapped call sites. A hanging LLM call must be cut short by `timeout`;
    the gate then falls back to "unclear" rather than propagating the timeout
    or hanging the turn — and, per the existing never-default-to-green-light
    rule, an unclear/timed-out answer must never resolve to "yes" or "adult"."""

    llm = HangingLLMClient(delay_seconds=3600.0)
    start = time.monotonic()
    optin = await interpret_optin_reply(
        "sounds like something worth knowing", llm=llm, model="x", use_llm=True, timeout=0.05
    )
    age = await interpret_age_reply(
        "still in school", llm=llm, model="x", use_llm=True, timeout=0.05
    )
    elapsed = time.monotonic() - start
    assert elapsed < 5.0, "gate interpreters waited far longer than the configured timeout"
    assert llm.calls == 2
    assert optin == "unclear"
    assert age == "unclear"
