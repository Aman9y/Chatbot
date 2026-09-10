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
    assert cfg.automatic_function_calling.disable is True  # no tools -> silence AFC warning


def test_gemini_requires_key_from_env():
    from app.services.llm.gemini_client import GeminiLLMClient

    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        GeminiLLMClient(Settings(llm_provider="gemini", gemini_api_key=""))


@pytest.fixture
def openrouter_stub(monkeypatch):
    """Capture both the AsyncOpenAI constructor kwargs and the create() call."""
    import openai

    created = AsyncMock(
        return_value=SimpleNamespace(
            id="gen-abc",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Hi from OpenRouter!"),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=6),
        )
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=created)),
        close=AsyncMock(),
    )
    ctor_kwargs: dict = {}

    def _factory(**kw):
        ctor_kwargs.update(kw)
        return client

    monkeypatch.setattr(openai, "AsyncOpenAI", _factory)
    return SimpleNamespace(create=created, ctor_kwargs=ctor_kwargs)


async def test_openrouter_adapter(openrouter_stub):
    from app.services.llm.openrouter_client import OpenRouterLLMClient

    client = OpenRouterLLMClient(
        Settings(
            llm_provider="openrouter",
            openrouter_api_key="sk-or-test",
            openrouter_app_title="leadbot",
            llm_temperature=0.4,
        )
    )
    resp = await client.complete(
        system="you are a bot",
        messages=MSGS,
        model="google/gemini-3.7-flash",
        max_output_tokens=500,
        temperature=0.4,
    )
    assert resp.text == "Hi from OpenRouter!"
    assert (resp.input_tokens, resp.output_tokens) == (20, 6)
    assert resp.provider == "openrouter"

    # constructed against the OpenRouter base URL with the OPENROUTER key
    assert openrouter_stub.ctor_kwargs["base_url"] == "https://openrouter.ai/api/v1"
    assert openrouter_stub.ctor_kwargs["api_key"] == "sk-or-test"
    assert openrouter_stub.ctor_kwargs["default_headers"] == {"X-Title": "leadbot"}

    # the model string passes through verbatim (provider-prefixed route)
    kwargs = openrouter_stub.create.call_args.kwargs
    assert kwargs["model"] == "google/gemini-3.7-flash"
    assert kwargs["messages"][0] == {"role": "system", "content": "you are a bot"}
    assert kwargs["temperature"] == 0.4


def test_openrouter_requires_key_from_env():
    from app.services.llm.openrouter_client import OpenRouterLLMClient

    with pytest.raises(LLMError, match="OPENROUTER_API_KEY"):
        OpenRouterLLMClient(Settings(llm_provider="openrouter", openrouter_api_key=""))


def test_openrouter_data_policy_flagged_until_confirmed():
    unconfirmed = Settings(
        llm_provider="openrouter",
        openrouter_api_key="sk-or-test",
        openrouter_data_policy_confirmed=False,
    )
    assert any("OpenRouter" in i for i in unconfirmed.unresolved_phase1_items)

    confirmed = Settings(
        llm_provider="openrouter",
        openrouter_api_key="sk-or-test",
        openrouter_data_policy_confirmed=True,
    )
    assert not any("OpenRouter" in i for i in confirmed.unresolved_phase1_items)


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
    s = Settings(
        llm_provider="gemini",
        gemini_model="gemini-x-flash",
        gemini_classifier_model="gemini-x-lite",
    )
    assert reply_model(s) == "gemini-x-flash"
    assert classifier_model(s) == "gemini-x-lite"
    # the shipped default is the auto-tracking alias
    assert Settings.model_fields["gemini_model"].default == "gemini-flash-latest"

    # openrouter
    ors = Settings(llm_provider="openrouter", openrouter_api_key="k")
    assert build_llm_client(ors).provider == "openrouter"
    assert reply_model(ors) == "google/gemini-3.7-flash"
    assert classifier_model(ors) == "google/gemini-3.7-flash"
    assert Settings.model_fields["openrouter_model"].default == "google/gemini-3.7-flash"
