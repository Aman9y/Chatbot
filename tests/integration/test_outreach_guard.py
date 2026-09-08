import pytest

from app.config import get_settings
from app.errors import OutreachBlocked, ServiceWindowClosed
from app.models.enums import (
    ConsentGate,
    ConsentStatus,
    LifecycleState,
    MinorPolicyStatus,
    MinorStatus,
    SentBy,
)
from app.models.lead import Lead
from app.services.outreach import OutreachService
from app.services.windows import WindowService


@pytest.fixture
def service(session, redis_client, wa_client):
    return OutreachService(session, redis_client, get_settings(), wa_client)


async def _verified_lead(session, phone="+919812345670") -> Lead:
    lead = Lead(
        phone_e164=phone,
        consent_status=ConsentStatus.OPTED_IN,
        consent_verified=True,
        consent_gate=ConsentGate.CLEARED,
    )
    session.add(lead)
    await session.flush()
    return lead


async def test_imported_unverified_consent_is_blocked(session, service):
    lead = Lead(phone_e164="+919812345671", consent_status=ConsentStatus.OPTED_IN)
    session.add(lead)
    await session.flush()
    decision = service.evaluate(lead)
    assert not decision.allowed
    assert "consent_not_verified" in decision.hard_blocks


async def test_unknown_consent_is_blocked(session, service):
    lead = Lead(phone_e164="+919812345672", consent_status=ConsentStatus.UNKNOWN)
    session.add(lead)
    await session.flush()
    assert "consent_unknown" in service.evaluate(lead).hard_blocks


async def test_verified_lead_is_allowed(session, service):
    lead = await _verified_lead(session)
    assert service.evaluate(lead).allowed is True


async def test_minor_pending_review_blocked(session, service):
    lead = await _verified_lead(session, "+919812345673")
    lead.is_minor = MinorStatus.YES
    lead.minor_policy_status = MinorPolicyStatus.PENDING_REVIEW
    decision = service.evaluate(lead)
    assert not decision.allowed
    assert "minor_policy_pending_review" in decision.hard_blocks


async def test_minor_cleared_is_allowed(session, service):
    lead = await _verified_lead(session, "+919812345674")
    lead.is_minor = MinorStatus.YES
    lead.minor_policy_status = MinorPolicyStatus.CLEARED_PARENT_CONSENT
    assert service.evaluate(lead).allowed is True


async def test_human_owned_blocked(session, service):
    lead = await _verified_lead(session, "+919812345675")
    lead.human_owned = True
    assert "human_owned" in service.evaluate(lead).hard_blocks


async def test_handoff_state_blocked(session, service):
    lead = await _verified_lead(session, "+919812345676")
    lead.lifecycle_state = LifecycleState.HANDOFF
    assert "human_handoff_in_progress" in service.evaluate(lead).hard_blocks


async def test_send_template_success_path(session, service, wa_client):
    lead = await _verified_lead(session, "+919812345677")
    msg = await service.send_template(
        lead, template_name="welcome_v1", language="en", variables={"body": ["Priya"]}
    )
    assert msg.wa_message_id.startswith("wamid.FAKE")
    assert lead.lifecycle_state == LifecycleState.CONTACTED
    assert wa_client.last()["template_name"] == "welcome_v1"


async def test_send_template_is_idempotent(session, service, wa_client):
    lead = await _verified_lead(session, "+919812345678")
    a = await service.send_template(lead, template_name="welcome_v1", language="en")
    b = await service.send_template(lead, template_name="welcome_v1", language="en")
    assert a.id == b.id
    assert len(wa_client.sent) == 1


async def test_bot_text_requires_open_window(session, service, redis_client):
    lead = await _verified_lead(session, "+919812345679")
    lead.lifecycle_state = LifecycleState.ENGAGED
    with pytest.raises(ServiceWindowClosed):
        await service.send_text(lead, text="hi there", actor=SentBy.BOT)

    await WindowService(redis_client, get_settings()).touch(lead)
    msg = await service.send_text(lead, text="hi there", actor=SentBy.BOT)
    assert msg.body == "hi there"


async def test_opted_out_lead_blocked(session, service):
    lead = Lead(phone_e164="+919812345680", lifecycle_state=LifecycleState.OPTED_OUT)
    session.add(lead)
    await session.flush()
    with pytest.raises(OutreachBlocked):
        await service.send_template(lead, template_name="x", language="en")
