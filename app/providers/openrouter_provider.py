# OpenRouter provider — connects to 100+ free and paid models
# through one single API. We use LLaMA 3.3 70B which is free.

import httpx
from app.providers.base import BaseLLMProvider
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class OpenRouterProvider(BaseLLMProvider):

    def __init__(self):
        self.api_key = settings.OPENROUTER_API_KEY
        # This model is free on OpenRouter — powerful and fast
        self.model = "meta-llama/llama-3.3-70b-instruct:free"
        self.base_url = "https://openrouter.ai/api/v1/chat/completions"
        logger.info("openrouter provider ready", model=self.model)

    async def complete(self, system: str, messages: list) -> str:
        # Format messages with system prompt first
        formatted = [{"role": "system", "content": system}]
        for msg in messages:
            formatted.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", "")
            })

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # OpenRouter needs these to identify your app
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "ABIP"
        }

        payload = {
            "model": self.model,
            "messages": formatted,
            "max_tokens": 2048,
            "temperature": 0.7,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    self.base_url,
                    headers=headers,
                    json=payload
                )
                response.raise_for_status()
                data = response.json()
                result = data["choices"][0]["message"]["content"]
                logger.info("openrouter response received", chars=len(result))
                return result

        except Exception as e:
            logger.error("openrouter completion failed", error=str(e))
            raise

    async def embed(self, text: str) -> list[float]:
        # OpenRouter doesn't support embeddings yet
        logger.warning("embeddings not supported by openrouter")
        return []