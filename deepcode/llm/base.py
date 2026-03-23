"""Base classes and data models for LLM clients."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from pydantic import BaseModel, Field


class LLMMessage(BaseModel):
    """A single message in an LLM conversation."""

    role: str = Field(description="Message role: 'system', 'user', or 'assistant'")
    content: str = Field(description="Message content")

    @classmethod
    def system(cls, content: str) -> LLMMessage:
        """Create a system message."""
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> LLMMessage:
        """Create a user message."""
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str) -> LLMMessage:
        """Create an assistant message."""
        return cls(role="assistant", content=content)


class LLMResponse(BaseModel):
    """Response from an LLM completion request."""

    content: str = Field(description="The generated text")
    model: str = Field(description="Model that produced the response")
    input_tokens: int = Field(default=0, description="Number of input tokens")
    output_tokens: int = Field(default=0, description="Number of output tokens")
    finish_reason: str = Field(default="stop", description="Reason the generation stopped")


class BaseLLMClient(ABC):
    """Abstract base class for LLM client implementations.

    All concrete LLM clients must implement :meth:`complete` and
    :meth:`stream_complete`.
    """

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Send a completion request and return the full response.

        Args:
            messages: Conversation history including the latest user message.
            temperature: Override the default temperature.
            max_tokens: Override the default max tokens.

        Returns:
            The LLM response.
        """

    @abstractmethod
    async def stream_complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Stream a completion response token by token.

        Args:
            messages: Conversation history including the latest user message.
            temperature: Override the default temperature.
            max_tokens: Override the default max tokens.

        Yields:
            Individual text chunks as they are received.
        """
