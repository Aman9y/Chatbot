"""complete_with_timeout() itself — the shared timeout backstop.

Separate from tests/unit/test_speaker_and_booking.py, test_extraction.py and
test_consent_gate.py, which exercise the four real call sites and their own
graceful (catch-and-fall-back) behaviour on a timeout. This file tests the
wrapper in isolation: it must always raise LLMError on a timeout, and it must
always log a WARNING identifying the call site (purpose), the model, the
provider, and the timeout duration — regardless of what any particular
caller's own exception handling then does with that LLMError. That log line
is the only guaranteed visibility into a timeout for the four call sites
that catch it broadly and fall back silently (speaker/extraction/
consent-gate/booking); without it, a repeat of the 9c1a6ae2 production
incident would again look like nothing happened in the logs.
"""

from __future__ import annotations

import logging

import pytest

from app.services.llm.base import LLMError, LLMMessage, complete_with_timeout
from app.services.llm.fake import FakeLLMClient
from tests.helpers import HangingLLMClient

MSGS = [LLMMessage(role="user", content="hi")]


async def test_timeout_raises_llm_error():
    llm = HangingLLMClient(delay_seconds=3600.0)
    with pytest.raises(LLMError, match="timed out"):
        await complete_with_timeout(
            llm,
            timeout=0.05,
            system="s",
            messages=MSGS,
            model="fake-model",
            max_output_tokens=10,
            purpose="extraction",
        )


async def test_timeout_logs_a_warning_with_call_site_and_duration(caplog):
    llm = HangingLLMClient(delay_seconds=3600.0)
    with caplog.at_level(logging.WARNING, logger="app.services.llm.base"):
        with pytest.raises(LLMError):
            await complete_with_timeout(
                llm,
                timeout=0.05,
                system="s",
                messages=MSGS,
                model="fake-model",
                max_output_tokens=10,
                purpose="extraction",
            )

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "expected a WARNING logged from complete_with_timeout itself"
    rec = warnings[0]
    assert "timed out" in rec.getMessage()
    # the fields a worker log needs to identify *which* call hung and for how long
    assert rec.extra_fields["llm_provider"] == "hanging"
    assert rec.extra_fields["llm_purpose"] == "extraction"
    assert rec.extra_fields["llm_model"] == "fake-model"
    assert rec.extra_fields["llm_timeout_seconds"] == 0.05


async def test_timeout_warning_survives_a_caller_that_swallows_the_error(caplog):
    """Mirrors what the four real call sites do: catch LLMError broadly and
    fall back. The WARNING must still have been logged even though nothing
    propagates out of this block."""

    llm = HangingLLMClient(delay_seconds=3600.0)
    with caplog.at_level(logging.WARNING, logger="app.services.llm.base"):
        try:
            await complete_with_timeout(
                llm,
                timeout=0.05,
                system="s",
                messages=MSGS,
                model="fake-model",
                max_output_tokens=10,
                purpose="speaker",
            )
        except Exception:  # noqa: BLE001 - deliberately mirrors the real callers
            pass

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "the timeout must be visible in logs even when the caller swallows it"
    assert warnings[0].extra_fields["llm_purpose"] == "speaker"


async def test_no_timeout_configured_is_unbounded_and_silent(caplog):
    """timeout=None (no Settings object handy, mainly tests) is a plain
    passthrough — no wait_for, and nothing to warn about."""

    llm = FakeLLMClient()
    with caplog.at_level(logging.WARNING, logger="app.services.llm.base"):
        resp = await complete_with_timeout(
            llm, timeout=None, system="s", messages=MSGS, model="m", max_output_tokens=10
        )
    assert resp.text
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


async def test_success_within_timeout_is_silent(caplog):
    llm = FakeLLMClient()
    with caplog.at_level(logging.WARNING, logger="app.services.llm.base"):
        resp = await complete_with_timeout(
            llm, timeout=5.0, system="s", messages=MSGS, model="m", max_output_tokens=10
        )
    assert resp.text
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
