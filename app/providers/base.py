# This file defines the interface every LLM provider must follow.
# Think of it as a contract — Gemini, OpenAI, Ollama all must have
# these exact same methods so agents never care which one is active.
# Agents just call provider.complete() and get a response back.

from abc import ABC, abstractmethod


class BaseLLMProvider(ABC):

    @abstractmethod
    async def complete(self, system: str, messages: list) -> str:
        # Send a conversation to the LLM and get a text response back
        # system = instructions telling the LLM how to behave
        # messages = list of {"role": "user/assistant", "content": "..."}
        pass

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        # Convert text into a list of numbers (vector embedding)
        # Used by the RAG agent to search similar documents
        pass