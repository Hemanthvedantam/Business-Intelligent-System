# Groq provider — fast, free LLaMA 3.3 70B model
# Has rate limits on free tier so we add retry logic
# to handle temporary rate limit errors automatically

import asyncio
from groq import AsyncGroq
from app.providers.base import BaseLLMProvider
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class GroqProvider(BaseLLMProvider):

    def __init__(self):
        self.client = AsyncGroq(api_key=settings.GROQ_API_KEY)
        # llama-3.3-70b is the most capable free model on Groq
        self.model = "llama-3.3-70b-versatile"
        logger.info("groq provider ready", model=self.model)

    async def complete(self, system: str, messages: list) -> str:
        formatted = [{"role": "system", "content": system}]
        for msg in messages:
            formatted.append({
                "role": msg.get("role", "user"),
                "content": msg.get("content", "")
            })

        # Retry up to 5 times with increasing delay on rate limit
        for attempt in range(5):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=formatted,
                    max_tokens=2048,
                    temperature=0.7,
                )
                result = response.choices[0].message.content
                logger.info("groq response received", chars=len(result))
                return result

            except Exception as e:
                error_str = str(e).lower()

                # Rate limit hit — wait and retry
                if "rate limit" in error_str or "429" in error_str or "too many" in error_str:
                    wait = (attempt + 1) * 10
                    logger.warning(f"groq rate limit — waiting {wait}s (attempt {attempt + 1}/5)")
                    await asyncio.sleep(wait)
                    continue

                # Any other error — log and raise immediately
                logger.error("groq completion failed", error=str(e))
                raise

        raise Exception("Groq failed after 5 attempts due to rate limits")

    async def embed(self, text: str) -> list[float]:
        # Groq doesn't support embeddings yet
        logger.warning("embeddings not supported by groq")
        return []