"""OpenAI LLM client implementation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from tenacity import retry, stop_after_attempt, wait_exponential

from deepcode.exceptions import ConfigurationError, LLMError
from deepcode.llm.base import BaseLLMClient, LLMMessage, LLMResponse
from deepcode.logging_config import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class OpenAIClient(BaseLLMClient):
    """LLM client backed by the OpenAI API (or compatible endpoints).

    Args:
        api_key: OpenAI API key.
        model: Model identifier (e.g. ``"gpt-4o-mini"``).
        base_url: Optional custom base URL for compatible APIs.
        temperature: Default sampling temperature.
        max_tokens: Default maximum tokens to generate.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> None:
        if not api_key:
            raise ConfigurationError(
                "DEEPCODE_LLM_API_KEY is required when using the OpenAI provider. "
                "Set it in your .env file or as an environment variable."
            )

        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ImportError("openai package is required: pip install openai") from exc

        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url

        self._client = AsyncOpenAI(**kwargs)
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        reraise=True,
    )
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Send a completion request to OpenAI.

        Args:
            messages: Conversation history.
            temperature: Override default temperature.
            max_tokens: Override default max tokens.

        Returns:
            :class:`LLMResponse` with the generated content.

        Raises:
            LLMError: On API errors.
        """
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                temperature=temperature if temperature is not None else self._temperature,
                max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
            )
            choice = response.choices[0]
            usage = response.usage
            return LLMResponse(
                content=choice.message.content or "",
                model=response.model,
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
                finish_reason=choice.finish_reason or "stop",
            )
        except Exception as exc:
            logger.error("OpenAI completion failed", error=str(exc))
            raise LLMError(f"OpenAI completion failed: {exc}") from exc

    async def stream_complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Stream a completion from OpenAI token by token.

        Args:
            messages: Conversation history.
            temperature: Override default temperature.
            max_tokens: Override default max tokens.

        Yields:
            Text delta chunks.

        Raises:
            LLMError: On API errors.
        """
        try:
            stream = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                temperature=temperature if temperature is not None else self._temperature,
                max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as exc:
            logger.error("OpenAI streaming failed", error=str(exc))
            raise LLMError(f"OpenAI streaming failed: {exc}") from exc
