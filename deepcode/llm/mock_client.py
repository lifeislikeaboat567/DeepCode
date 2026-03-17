"""Mock LLM client for testing without real API calls."""

from __future__ import annotations

from collections.abc import AsyncIterator

from deepcode.llm.base import BaseLLMClient, LLMMessage, LLMResponse


class MockLLMClient(BaseLLMClient):
    """A deterministic mock LLM client for unit tests.

    Args:
        responses: An optional list of responses to return in sequence.
            After the list is exhausted, a default response is returned.
    """

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses or [])
        self._call_count = 0

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Return the next pre-configured response.

        Args:
            messages: Ignored in mock mode.
            temperature: Ignored in mock mode.
            max_tokens: Ignored in mock mode.

        Returns:
            A mock :class:`LLMResponse`.
        """
        if self._responses and self._call_count < len(self._responses):
            content = self._responses[self._call_count]
        else:
            content = "Mock response: task completed successfully."

        self._call_count += 1
        return LLMResponse(
            content=content,
            model="mock-model",
            input_tokens=10,
            output_tokens=len(content.split()),
        )

    async def stream_complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Stream the next pre-configured response word by word.

        Args:
            messages: Ignored in mock mode.
            temperature: Ignored in mock mode.
            max_tokens: Ignored in mock mode.

        Yields:
            Individual words from the mock response.
        """
        response = await self.complete(messages)
        for word in response.content.split():
            yield word + " "
