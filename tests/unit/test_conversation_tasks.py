"""process_lead_turn must never go silent: every path out of it — success,
lock-busy retry, or a genuine failure — logs one unambiguous final line.

Before this, an exception raised anywhere inside ``_run`` (building the
WhatsApp/LLM client, the DB session, the engine itself) propagated out of the
task with nothing beyond Celery's own "received" line to show for it in a
worker log — this is exactly the gap a real production incident hit (a
360dialog send failing silently). These tests exercise the task wrapper
directly (not the inner ``run_lead_turn``, which already has its own
coverage in test_turn_dispatch.py) with ``_run`` mocked, so they isolate the
new logging/re-raise behaviour from the full engine.
"""

from __future__ import annotations

import logging

import pytest

import app.scheduler.conversation_tasks as ct
from app.config import Settings
from app.services.conversation.dispatch import LockUnavailable


def _settings(**over) -> Settings:
    base = dict(turn_lock_retry_seconds=0.0, turn_lock_max_retries=1)
    base.update(over)
    return Settings(**base)


def test_success_logs_a_clear_final_outcome(monkeypatch, caplog):
    monkeypatch.setattr(ct, "get_settings", lambda: _settings())

    async def fake_run(lead_id: str) -> dict:
        return {"lead_id": lead_id, "action": "sent", "skipped_reason": None, "booking": False}

    monkeypatch.setattr(ct, "_run", fake_run)

    with caplog.at_level(logging.INFO, logger="app.scheduler.conversation_tasks"):
        result = ct.process_lead_turn.run("lead-123")

    assert result["action"] == "sent"
    messages = [r.message for r in caplog.records]
    assert any("process_lead_turn started" in m for m in messages)
    assert any("process_lead_turn finished" in m and "action=sent" in m for m in messages)


def test_failure_is_logged_with_traceback_and_reraised(monkeypatch, caplog):
    monkeypatch.setattr(ct, "get_settings", lambda: _settings())

    async def fake_run(lead_id: str) -> dict:
        raise RuntimeError("WHATSAPP_CLIENT=360dialog requires D360_API_KEY")

    monkeypatch.setattr(ct, "_run", fake_run)

    with caplog.at_level(logging.INFO, logger="app.scheduler.conversation_tasks"):
        with pytest.raises(RuntimeError, match="D360_API_KEY"):
            ct.process_lead_turn.run("lead-456")

    failure_records = [
        r for r in caplog.records if r.levelno >= logging.ERROR and "FAILED" in r.message
    ]
    assert failure_records, caplog.text
    rec = failure_records[0]
    assert "RuntimeError" in rec.message
    assert rec.exc_info is not None  # full traceback captured, not swallowed


def test_lock_busy_is_logged_before_retrying(monkeypatch, caplog):
    monkeypatch.setattr(ct, "get_settings", lambda: _settings(turn_lock_max_retries=0))

    async def fake_run(lead_id: str) -> dict:
        raise LockUnavailable(lead_id)

    monkeypatch.setattr(ct, "_run", fake_run)

    with caplog.at_level(logging.INFO, logger="app.scheduler.conversation_tasks"):
        # celery's own retry machinery needs a bound task context; .apply()
        # provides one and surfaces the eventual MaxRetriesExceededError
        # rather than a bare Retry control-flow exception.
        result = ct.process_lead_turn.apply(args=["lead-789"])

    assert not result.successful()
    messages = [r.message for r in caplog.records]
    assert any("lock busy, retrying" in m for m in messages)
