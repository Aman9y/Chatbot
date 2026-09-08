from __future__ import annotations

from app.config import Settings
from app.services.llm.base import LLMClient
from app.services.llm.fake import FakeLLMClient


def build_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "anthropic":
        from app.services.llm.anthropic_client import AnthropicLLMClient

        return AnthropicLLMClient(settings)
    if settings.llm_provider == "openai":
        from app.services.llm.openai_client import OpenAILLMClient

        return OpenAILLMClient(settings)
    if settings.llm_provider == "gemini":
        from app.services.llm.gemini_client import GeminiLLMClient

        return GeminiLLMClient(settings)
    return FakeLLMClient()


def classifier_model(settings: Settings) -> str:
    if settings.llm_provider == "openai":
        return settings.openai_classifier_model
    if settings.llm_provider == "gemini":
        return settings.gemini_classifier_model
    return settings.anthropic_classifier_model


def reply_model(settings: Settings) -> str:
    if settings.llm_provider == "openai":
        return settings.openai_model
    if settings.llm_provider == "gemini":
        return settings.gemini_model
    return settings.anthropic_model
