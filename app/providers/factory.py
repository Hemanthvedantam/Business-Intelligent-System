from functools import lru_cache
from app.core.config import settings
from app.providers.base import BaseLLMProvider


@lru_cache()
def get_provider() -> BaseLLMProvider:
    provider = settings.LLM_PROVIDER.lower()

    if provider == "groq":
        from app.providers.groq_provider import GroqProvider
        return GroqProvider()

    if provider == "gemini":
        from app.providers.gemini import GeminiProvider
        return GeminiProvider()

    if provider == "openrouter":
        from app.providers.openrouter_provider import OpenRouterProvider
        return OpenRouterProvider()

    raise ValueError(f"Unknown LLM provider: {provider}")