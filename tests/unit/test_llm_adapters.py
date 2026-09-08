"""Adapter request-shaping / response-parsing, with the vendor SDK monkeypatched.

No network. Verifies the Anthropic and OpenAI adapters build the right request
and map the response into an LLMResponse.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.services.llm.base import LLMError, LLMMessage

MSGS = [LLMMessage(role="user", content="hello")]


@pytest.fixture
def anthropic_stub(monkeypatch):
    import anthropic

    created = AsyncMock(
        return_value=SimpleNamespace(
            id="msg_1",
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="Hi there!")],
            usage=SimpleNamespace(input_tokens=11, output_tokens=4),
        )
    )
    client = SimpleNamespace(
        messages=SimpleNamespace(create=created), close=AsyncMock()
    )
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kw: client)
    return created


@pytest.fixture
def openai_stub(monkeypatch):
    import openai

    created = AsyncMock(
        return_value=SimpleNamespace(
            id="cmpl_1",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Hi there!"), finish_reason="stop"
                )
            ],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4),
        )
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=created)),
        close=AsyncMock(),
    )
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kw: client)
    return created


async def test_anthropic_adapter(anthropic_stub):
    from app.services.llm.anthropic_client import AnthropicLLMClient

    client = AnthropicLLMClient(Settings(anthropic_api_key="k", anthropic_effort="low"))
    resp = await client.complete(
        system="you are a bot", messages=MSGS, model="claude-opus-5", max_output_tokens=500
    )
    assert resp.text == "Hi there!"
    assert (resp.input_tokens, resp.output_tokens) == (11, 4)
    assert resp.provider == "anthropic"

    kwargs = anthropic_stub.call_args.kwargs
    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["system"] == "you are a bot"
    assert kwargs["messages"] == [{"role": "user", "content": "hello"}]
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"] == {"effort": "low"}
    assert "temperature" not in kwargs  # rejected on the Claude 5 family


async def test_anthropic_adapter_haiku_skips_effort(anthropic_stub):
    from app.services.llm.anthropic_client import AnthropicLLMClient

    client = AnthropicLLMClient(Settings(anthropic_api_key="k"))
    await client.complete(
        system="s", messages=MSGS, model="claude-haiku-4-5", max_output_tokens=40
    )
    kwargs = anthropic_stub.call_args.kwargs
    assert "thinking" not in kwargs
    assert "output_config" not in kwargs


@pytest.fixture
def gemini_stub(monkeypatch):
    from google import genai

    created = AsyncMock(
        return_value=SimpleNamespace(
            response_id="resp_1",
            text="Hi there!",
            candidates=[SimpleNamespace(finish_reason="STOP")],
            usage_metadata=SimpleNamespace(
                prompt_token_count=13, candidates_token_count=4
            ),
        )
    )
    client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content=created),
            aclose=AsyncMock(),
        )
    )
    monkeypatch.setattr(genai, "Client", lambda **kw: client)
    return created


async def test_gemini_adapter(gemini_stub):
    from app.services.llm.gemini_client import GeminiLLMClient

    client = GeminiLLMClient(Settings(gemini_api_key="k", llm_temperature=0.4))
    resp = await client.complete(
        system="you are a bot",
        messages=[LLMMessage(role="user", content="hello"), LLMMessage(role="assistant", content="hi")],
        model="gemini-2.5-flash",
        max_output_tokens=500,
        temperature=0.4,
        json_mode=True,
    )
    assert resp.text == "Hi there!"
    assert (resp.input_tokens, resp.output_tokens) == (13, 4)
    assert resp.provider == "gemini"

    kwargs = gemini_stub.call_args.kwargs
    assert kwargs["model"] == "gemini-2.5-flash"
    assert kwargs["contents"][0] == {"role": "user", "parts": [{"text": "hello"}]}
    assert kwargs["contents"][1] == {"role": "model", "parts": [{"text": "hi"}]}
    cfg = kwargs["config"]
    assert cfg.system_instruction == "you are a bot"
    assert cfg.max_output_tokens == 500
    assert cfg.temperature == 0.4
    assert cfg.response_mime_type == "application/json"


def test_gemini_requires_key_from_env():
    from app.services.llm.gemini_client import GeminiLLMClient

    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        GeminiLLMClient(Settings(llm_provider="gemini", gemini_api_key=""))


async def test_openai_adapter(openai_stub):
    from app.services.llm.openai_client import OpenAILLMClient

    client = OpenAILLMClient(Settings(openai_api_key="k", llm_temperature=0.3))
    resp = await client.complete(
        system="you are a bot",
        messages=MSGS,
        model="gpt-4o",
        max_output_tokens=500,
        temperature=0.3,
        json_mode=True,
    )
    assert resp.text == "Hi there!"
    assert (resp.input_tokens, resp.output_tokens) == (12, 4)
    assert resp.provider == "openai"

    kwargs = openai_stub.call_args.kwargs
    assert kwargs["model"] == "gpt-4o"
    assert kwargs["messages"][0] == {"role": "system", "content": "you are a bot"}
    assert kwargs["messages"][1] == {"role": "user", "content": "hello"}
    assert kwargs["temperature"] == 0.3
    assert kwargs["response_format"] == {"type": "json_object"}


def test_factory_selects_provider(gemini_stub):
    from app.services.llm.factory import (
        build_llm_client,
        classifier_model,
        reply_model,
    )

    assert build_llm_client(Settings(llm_provider="fake")).provider == "fake"
    assert (
        build_llm_client(
            Settings(llm_provider="gemini", gemini_api_key="k")
        ).provider
        == "gemini"
    )
    s = Settings(llm_provider="gemini")
    assert reply_model(s) == "gemini-2.5-flash"
    assert classifier_model(s) == "gemini-2.5-flash-lite"
