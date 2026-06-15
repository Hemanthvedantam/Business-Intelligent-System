# This is the Gemini implementation of our LLM provider.
# All agents talk to this file — never to Gemini directly.
# If we switch to OpenAI later, only this file changes.

import google.generativeai as genai
from app.providers.base import BaseLLMProvider
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class GeminiProvider(BaseLLMProvider):

    def __init__(self):
        # Configure Gemini with our API key when this class is created
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self.model = genai.GenerativeModel(settings.GEMINI_MODEL)
        logger.info("gemini provider ready", model=settings.GEMINI_MODEL)

    async def complete(self, system: str, messages: list) -> str:
        # Build the full prompt by combining system instructions + messages
        # Gemini takes a single string so we format it clearly
        full_prompt = f"{system}\n\n"

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            # Format each message so Gemini understands who said what
            full_prompt += f"{role.upper()}: {content}\n"

        try:
            response = self.model.generate_content(full_prompt)
            result = response.text
            logger.info("gemini response received", chars=len(result))
            return result
        except Exception as e:
            logger.error("gemini completion failed", error=str(e))
            raise

    async def embed(self, text: str) -> list[float]:
        # Convert text to a vector for semantic search in Qdrant
        try:
            result = genai.embed_content(
                model="models/text-embedding-004",
                content=text,
            )
            return result["embedding"]
        except Exception as e:
            logger.error("gemini embedding failed", error=str(e))
            raise