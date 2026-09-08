from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import Settings
from app.models.enums import ConsentStatus, LifecycleState
from app.models.lead import Lead
from app.scheduler.sweeps import (
    SweepDeps,
    advance_engagement,
    expire_windows,
    run_reengagement,
    send_in_window_nudges,
)
from app.services import state_machine
from app.services.timeutils import utcnow


def _settings(**over) -> Settings:
    base = dict(
        llm_provider="fake",
        bot_autoreply_enabled=True,
        nudge_after_hours=6.0,
        nudge_max_per_window=1,
        reengagement_spacing_hours="20,48,72",
        silent_retry_max_rounds=3,
        nurture_max_rounds=2,
        nurture_gap_days=4.0,
        counselor_name="Dr. Rao",
    )
    base.update(over)
    return Settings(**base)


@pytest.fixture
def deps(session, redis_client, wa_client):
    def _make(settings=None):
        return SweepDeps(
            session=session,
            redis=redis_client,
            settings=settings or _settings(),
            wa_client=wa_client,
        )

    return _make


async def _lead(session, **kw) -> Lead:
    defaults = dict(phone_e164=f"+9198123{utcnow().microsecond:05d}"[:14])
    defaults.update(kw)
    lead = Lead(**defaults)
    session.add(lead)
    await session.flush()
    return lead


# --- expire_windows ---------------------------------------------------
async def test_expire_windows_moves_engaged_to_silent(deps, session):
    now = utcnow()
    lead = await _lead(
        session,
        phone_e164="+919812345670",
        lifecycle_state=LifecycleState.ENGAGED,
        first_engaged_at=now - timedelta(hours=30),
        service_window_expires_at=now - timedelta(minutes=5),
    )
    await session.commit()

    result = await expire_windows(deps())
    assert result.acted == 1
    await session.refresh(lead)
    assert lead.lifecycle_state == LifecycleState.SILENT
    assert lead.next_reengagement_at is not None


async def test_expire_windows_leaves_open_windows(deps, session):
    now = utcnow()
    lead = await _lead(
        session,
        phone_e164="+919812345671",
        lifecycle_state=LifecycleState.ENGAGED,
        service_window_expires_at=now + timedelta(hours=5),
    )
    await session.commit()
    await expire_windows(deps())
    await session.refresh(lead)
    assert lead.lifecycle_state == LifecycleState.ENGAGED


# --- advance_engagement --------------------------------------------
async def test_advance_engagement_notifies_at_handoff_phase(deps, session):
    from app.models.handoff import HandoffNotification

    now = utcnow()
    lead = await _lead(
        session,
        phone_e164="+919812345672",
        lifecycle_state=LifecycleState.ENGAGED,
        first_engaged_at=now - timedelta(hours=30),
    )
    await session.commit()

    result = await advance_engagement(deps())
    assert result.acted == 1
    await session.refresh(lead)
    assert lead.phase_handoff_notified is True
    note = await session.scalar(
        select(HandoffNotification).where(HandoffNotification.lead_id == lead.id)
    )
    assert note.trigger.value == "phase_handoff"

    # idempotent — second run does not re-notify
    result2 = await advance_engagement(deps())
    assert result2.acted == 0


async def test_advance_engagement_moves_to_nurture_at_48h(deps, session):
    now = utcnow()
    lead = await _lead(
        session,
        phone_e164="+919812345673",
        lifecycle_state=LifecycleState.ENGAGED,
        first_engaged_at=now - timedelta(hours=50),
    )
    await session.commit()

    await advance_engagement(deps())
    await session.refresh(lead)
    assert lead.lifecycle_state == LifecycleState.NURTURE
    assert lead.next_reengagement_at is not None


# --- send_in_window_nudges ----------------------------------------
async def test_nudge_sent_to_quiet_engaged_lead(deps, session, wa_client):
    now = utcnow()
    lead = await _lead(
        session,
        phone_e164="+919812345674",
        lifecycle_state=LifecycleState.ENGAGED,
        consent_status=ConsentStatus.OPTED_IN,
        consent_verified=True,
        first_engaged_at=now - timedelta(hours=8),
        last_inbound_at=now - timedelta(hours=8),
        service_window_expires_at=now + timedelta(hours=16),
    )
    await session.commit()

    result = await send_in_window_nudges(deps())
    assert result.acted == 1
    await session.refresh(lead)
    assert lead.nudge_count == 1
    assert lead.last_nudge_at is not None
    assert wa_client.sent[-1]["kind"] == "text"

    # second run: capped at nudge_max_per_window
    result2 = await send_in_window_nudges(deps())
    assert result2.acted == 0


async def test_nudge_skipped_when_not_quiet(deps, session):
    now = utcnow()
    await _lead(
        session,
        phone_e164="+919812345675",
        lifecycle_state=LifecycleState.ENGAGED,
        first_engaged_at=now - timedelta(hours=2),
        last_inbound_at=now - timedelta(minutes=30),
        service_window_expires_at=now + timedelta(hours=22),
    )
    await session.commit()
    result = await send_in_window_nudges(deps())
    assert result.acted == 0


# --- run_reengagement -------------------------------------------
async def _silent_lead(session, phone, *, round_=0, verified=True, due=True) -> Lead:
    now = utcnow()
    return await _lead(
        session,
        phone_e164=phone,
        lifecycle_state=LifecycleState.SILENT,
        consent_status=ConsentStatus.OPTED_IN if verified else ConsentStatus.UNKNOWN,
        consent_verified=verified,
        silent_retry_round=round_,
        next_reengagement_at=now - timedelta(hours=1) if due else now + timedelta(hours=5),
    )


async def test_reengagement_sends_template_and_bumps_round(deps, session, wa_client):
    lead = await _silent_lead(session, "+919812345676", round_=0)
    await session.commit()

    result = await run_reengagement(deps())
    assert result.acted == 1
    await session.refresh(lead)
    assert lead.silent_retry_round == 1
    assert lead.last_reengagement_at is not None
    assert wa_client.sent[-1]["kind"] == "template"
    assert wa_client.sent[-1]["template_name"] == "reengage_v1"


async def test_reengagement_exhausts_to_dormant(deps, session):
    lead = await _silent_lead(session, "+919812345677", round_=3)
    await session.commit()

    await run_reengagement(deps())
    await session.refresh(lead)
    assert lead.lifecycle_state == LifecycleState.DORMANT
    assert lead.dormant_at is not None


async def test_reengagement_blocked_for_unverified_consent(deps, session, wa_client):
    lead = await _silent_lead(session, "+919812345678", verified=False)
    await session.commit()

    result = await run_reengagement(deps())
    assert result.acted == 0
    await session.refresh(lead)
    assert lead.lifecycle_state == LifecycleState.SILENT
    assert lead.silent_retry_round == 0
    assert not wa_client.sent  # nothing sent


async def test_nurture_round_and_dormant(deps, session, wa_client):
    now = utcnow()
    lead = await _lead(
        session,
        phone_e164="+919812345679",
        lifecycle_state=LifecycleState.NURTURE,
        consent_status=ConsentStatus.OPTED_IN,
        consent_verified=True,
        nurture_round=0,
        next_reengagement_at=now - timedelta(hours=1),
    )
    await session.commit()

    await run_reengagement(deps())
    await session.refresh(lead)
    assert lead.nurture_round == 1
    assert wa_client.sent[-1]["template_name"] == "nurture_v1"

    lead.nurture_round = 2
    lead.next_reengagement_at = utcnow() - timedelta(hours=1)
    await session.commit()
    await run_reengagement(deps())
    await session.refresh(lead)
    assert lead.lifecycle_state == LifecycleState.DORMANT


# --- state-machine reset on re-engagement -----------------------
async def test_inbound_resets_scheduler_counters(session):
    lead = await _lead(
        session,
        phone_e164="+919812345680",
        lifecycle_state=LifecycleState.SILENT,
        silent_retry_round=2,
        nudge_count=1,
        phase_handoff_notified=True,
        next_reengagement_at=utcnow(),
    )
    await session.flush()

    from app.models.enums import LifecycleEvent

    await state_machine.apply_event(session, lead, LifecycleEvent.INBOUND_MESSAGE, actor="lead")
    assert lead.lifecycle_state == LifecycleState.ENGAGED
    assert lead.silent_retry_round == 0
    assert lead.nudge_count == 0
    assert lead.phase_handoff_notified is False
    assert lead.next_reengagement_at is None
