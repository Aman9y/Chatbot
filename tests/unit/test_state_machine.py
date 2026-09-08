import pytest

from app.errors import IllegalTransition
from app.models.enums import LifecycleEvent as E
from app.models.enums import LifecycleState as S
from app.models.lead import Lead
from app.services import state_machine
from app.services.state_machine import next_state


@pytest.mark.parametrize(
    "current,event,expected",
    [
        (S.NEVER_CONTACTED, E.OUTBOUND_TEMPLATE_SENT, S.CONTACTED),
        (S.NEVER_CONTACTED, E.INBOUND_MESSAGE, S.ENGAGED),
        (S.CONTACTED, E.INBOUND_MESSAGE, S.ENGAGED),
        (S.CONTACTED, E.SERVICE_WINDOW_EXPIRED, S.SILENT),
        (S.ENGAGED, E.BOOKING_CONFIRMED, S.HANDOFF),
        (S.ENGAGED, E.NURTURE_TIMEOUT, S.NURTURE),
        (S.ENGAGED, E.SERVICE_WINDOW_EXPIRED, S.SILENT),
        (S.SILENT, E.INBOUND_MESSAGE, S.ENGAGED),
        (S.SILENT, E.RETRY_ROUNDS_EXHAUSTED, S.DORMANT),
        (S.NURTURE, E.INBOUND_MESSAGE, S.ENGAGED),
        (S.DORMANT, E.INBOUND_MESSAGE, S.ENGAGED),
        (S.HANDOFF, E.INBOUND_MESSAGE, S.HANDOFF),
        (S.HANDOFF, E.HUMAN_RELEASE, S.ENGAGED),
        (S.ENGAGED, E.GATE_HELD, S.GATE_HOLD),
        (S.CONTACTED, E.GATE_HELD, S.GATE_HOLD),
        (S.GATE_HOLD, E.INBOUND_MESSAGE, S.GATE_HOLD),
        (S.GATE_HOLD, E.HUMAN_RELEASE, S.ENGAGED),
        (S.NEVER_CONTACTED, E.RETRY_ROUNDS_EXHAUSTED, S.DORMANT),
        (S.CONTACTED, E.RETRY_ROUNDS_EXHAUSTED, S.DORMANT),
    ],
)
def test_defined_transitions(current, event, expected):
    assert next_state(current, event) == expected


async def test_gate_held_marks_human_owned(session):
    lead = Lead(phone_e164="+919812345670", lifecycle_state=S.ENGAGED)
    session.add(lead)
    await session.flush()
    await state_machine.apply_event(session, lead, E.GATE_HELD, actor="system")
    assert lead.lifecycle_state == S.GATE_HOLD
    assert lead.human_owned is True


@pytest.mark.parametrize(
    "current",
    [S.NEVER_CONTACTED, S.CONTACTED, S.ENGAGED, S.SILENT, S.NURTURE, S.DORMANT, S.HANDOFF],
)
def test_opt_out_from_any_state(current):
    assert next_state(current, E.OPT_OUT) == S.OPTED_OUT


def test_opted_out_is_sticky():
    with pytest.raises(IllegalTransition):
        next_state(S.OPTED_OUT, E.INBOUND_MESSAGE)
    assert next_state(S.OPTED_OUT, E.OPT_OUT) == S.OPTED_OUT


def test_undefined_transition_raises():
    # you do not send a marketing template to a lead already in an open window
    with pytest.raises(IllegalTransition):
        next_state(S.ENGAGED, E.OUTBOUND_TEMPLATE_SENT)


async def test_apply_event_persists_audit_and_timestamps(session):
    lead = Lead(phone_e164="+919812345670")
    session.add(lead)
    await session.flush()

    await state_machine.apply_event(session, lead, E.OUTBOUND_TEMPLATE_SENT, actor="bot")
    assert lead.lifecycle_state == S.CONTACTED
    assert lead.contacted_at is not None

    await state_machine.apply_event(session, lead, E.INBOUND_MESSAGE, actor="lead")
    assert lead.lifecycle_state == S.ENGAGED
    assert lead.first_engaged_at is not None

    await state_machine.apply_event(session, lead, E.BOOKING_CONFIRMED, actor="bot")
    assert lead.lifecycle_state == S.HANDOFF
    assert lead.booked_at is not None
    assert lead.human_owned is True

    await session.refresh(lead, ["transitions"])
    assert [t.event for t in lead.transitions] == [
        E.OUTBOUND_TEMPLATE_SENT,
        E.INBOUND_MESSAGE,
        E.BOOKING_CONFIRMED,
    ]
