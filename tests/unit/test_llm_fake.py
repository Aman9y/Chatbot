import pytest

from app.services.llm.base import LLMError, LLMMessage
from app.services.llm.fake import FakeLLMClient

MSGS = [LLMMessage(role="user", content="hi")]


async def test_default_reply():
    llm = FakeLLMClient()
    r = await llm.complete(system="s", messages=MSGS, model="m", max_output_tokens=100)
    assert "counsellor" in r.text.lower()
    assert r.provider == "fake"
    assert llm.calls[0]["purpose"] == "reply"


async def test_reply_queue_consumed_in_order():
    llm = FakeLLMClient(replies=["first", "second"])
    a = await llm.complete(system="s", messages=MSGS, model="m", max_output_tokens=50)
    b = await llm.complete(system="s", messages=MSGS, model="m", max_output_tokens=50)
    c = await llm.complete(system="s", messages=MSGS, model="m", max_output_tokens=50)
    assert (a.text, b.text) == ("first", "second")
    assert c.text  # falls back to default once queue is empty


async def test_json_mode_returns_scripted_json():
    llm = FakeLLMClient(json_responses=[{"speaker": "parent"}])
    r = await llm.complete(
        system="classify", messages=MSGS, model="m", max_output_tokens=20,
        json_mode=True, purpose="speaker",
    )
    assert r.text == '{"speaker": "parent"}'


async def test_raise_error():
    llm = FakeLLMClient(raise_error=True)
    with pytest.raises(LLMError):
        await llm.complete(system="s", messages=MSGS, model="m", max_output_tokens=10)
