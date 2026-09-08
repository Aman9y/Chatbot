"""send_consent_asks sweep: the outbound opt-in drip (build-plan §2)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.config import Settings
from app.models.enums import ConsentGate, LifecycleState
from app.models.lead import Lead
from app.scheduler.sweeps import SweepDeps, send_consent_asks
from app.services.timeutils import utcnow


def _settings(**over) -> Settings:
    base = dict(
        llm_provider="fake",
        consent_gate_enabled=True,
        consent_ask_sweep_enabled=True,
        consent_ask_max_rounds=2,
        consent_ask_gap_hours=24.0,
        consent_asks_per_sweep=25,
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


async def _lead(session, phone, **kw) -> Lead:
    lead = Lead(phone_e164=phone, **kw)
    session.add(lead)
    await session.flush()
    return lead


async def test_sweep_off_by_default(deps, session, wa_client):
    await _lead(session, "+919812345670")
    await session.commit()
    result = await send_consent_asks(deps(_settings(consent_ask_sweep_enabled=False)))
    assert result.acted == 0
    assert not wa_client.sent


async def test_sends_opt_in_ask_and_moves_to_contacted(deps, session, wa_client):
    lead = await _lead(session, "+919812345671")
    await session.commit()

    result = await send_consent_asks(deps())
    assert result.acted == 1
    await session.refresh(lead)
    assert lead.lifecycle_state == LifecycleState.CONTACTED
    assert lead.consent_ask_count == 1
    assert lead.consent_ask_sent_at is not None
    assert lead.consent_gate == ConsentGate.PENDING_OPT_IN
    assert wa_client.sent[-1]["kind"] == "template"
    assert wa_client.sent[-1]["template_name"] == "gate_consent_v1"


async def test_not_resent_before_the_gap(deps, session, wa_client):
    await _lead(
        session,
        "+919812345672",
        lifecycle_state=LifecycleState.CONTACTED,
        consent_ask_count=1,
        consent_ask_sent_at=utcnow() - timedelta(hours=2),
    )
    await session.commit()
    result = await send_consent_asks(deps())
    assert result.acted == 0
    assert not wa_client.sent


async def test_exhausts_to_dormant(deps, session):
    lead = await _lead(
        session,
        "+919812345673",
        lifecycle_state=LifecycleState.CONTACTED,
        consent_ask_count=2,
        consent_ask_sent_at=utcnow() - timedelta(hours=48),
    )
    await session.commit()
    await send_consent_asks(deps())
    await session.refresh(lead)
    assert lead.lifecycle_state == LifecycleState.DORMANT


async def test_skips_opted_out_and_cleared(deps, session, wa_client):
    await _lead(
        session,
        "+919812345674",
        lifecycle_state=LifecycleState.OPTED_OUT,
        consent_gate=ConsentGate.REFUSED,
    )
    await _lead(session, "+919812345675", consent_gate=ConsentGate.CLEARED)
    await session.commit()
    result = await send_consent_asks(deps())
    assert result.acted == 0
    assert not wa_client.sent
