import pytest

from app.models.enums import RoleHint
from app.services.conversation.booking import detect_booking, heuristic_booking
from app.services.conversation.speaker import detect_speaker, heuristic_speaker
from app.services.llm.base import LLMMessage
from app.services.llm.fake import FakeLLMClient


@pytest.mark.parametrize(
    "text,expected",
    [
        ("My son scored 480 in NEET, what are his options?", RoleHint.PARENT),
        ("as a parent I'm worried about safety abroad", RoleHint.PARENT),
        ("I scored 520 and want to do MBBS abroad", RoleHint.STUDENT),
        ("my rank is 88000, is Georgia possible?", RoleHint.STUDENT),
        ("what is the process?", RoleHint.UNKNOWN),
    ],
)
def test_heuristic_speaker(text, expected):
    assert heuristic_speaker(text)[0] == expected


async def test_detect_speaker_falls_back_to_prior():
    role, method = await detect_speaker("ok thanks", prior_role=RoleHint.PARENT)
    assert role == RoleHint.PARENT
    assert method == "prior"


async def test_detect_speaker_uses_llm_when_unsure():
    llm = FakeLLMClient(json_responses={"speaker": {"speaker": "parent"}})
    role, method = await detect_speaker(
        "hello, following up on the enquiry", llm=llm, model="fake", use_llm=True
    )
    assert role == RoleHint.PARENT
    assert method == "llm"


@pytest.mark.parametrize(
    "text,detected",
    [
        ("ok call me tomorrow evening", True),
        ("yes, let's book the call", True),
        ("that works for me, call at 5pm", True),
        ("can you come to the office tomorrow", False),  # they ask us, not accept
        ("maybe later, need to think", False),
        ("what's the fee for Georgia", False),
    ],
)
def test_heuristic_booking(text, detected):
    assert heuristic_booking(text).detected is detected


async def test_detect_booking_heuristic_wins():
    sig = await detect_booking(
        [LLMMessage(role="user", content="ok book the call for tomorrow morning")],
        use_llm=False,
    )
    assert sig.detected
    assert sig.method == "heuristic"
    assert sig.proposed_time == "tomorrow"


async def test_detect_booking_llm_path():
    llm = FakeLLMClient(
        json_responses=[{"booking": True, "time": "Monday 4pm", "format": "call"}]
    )
    sig = await detect_booking(
        [
            LLMMessage(role="assistant", content="Would a call work?"),
            LLMMessage(role="user", content="sounds fine"),
        ],
        llm=llm,
        model="fake",
        use_llm=True,
    )
    assert sig.detected and sig.method == "llm" and sig.format == "call"


async def test_detect_booking_negative():
    llm = FakeLLMClient(json_responses=[{"booking": False, "time": None, "format": None}])
    sig = await detect_booking(
        [LLMMessage(role="user", content="just researching for now")],
        llm=llm,
        model="fake",
        use_llm=True,
    )
    assert not sig.detected
