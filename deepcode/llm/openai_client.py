"""OpenAI LLM client implementation."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from tenacity import retry, stop_after_attempt, wait_exponential

from deepcode.exceptions import ConfigurationError, LLMError
from deepcode.llm.base import BaseLLMClient, LLMMessage, LLMResponse
from deepcode.logging_config import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


def _read_field(value: object, key: str, default: object = None) -> object:
    """Read a field from either mapping-like or object-like values."""
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _stringify_text_value(value: object) -> str:
    """Convert common OpenAI-compatible text payload shapes into plain text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        # Common shape: {"value": "..."}
        nested = value.get("value")
        if isinstance(nested, str):
            return nested.strip()
        return ""
    return ""


def _coerce_tool_arguments(raw_arguments: object) -> dict:
    """Normalize tool-call arguments into a dictionary."""
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str):
        try:
            parsed = json.loads(raw_arguments)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        return {"input": raw_arguments}
    return {}


def _normalize_openai_message_content(choice: object) -> str:
    """Extract assistant content and synthesize JSON for native tool-calls.

    Some OpenAI-compatible models return empty `content` plus `tool_calls`.
    Our agent expects textual JSON, so we convert the first tool call to a
    function_call JSON payload.
    """
    message = _read_field(choice, "message", None)

    raw_content = _read_field(message, "content", "")
    content = ""
    if isinstance(raw_content, str):
        content = raw_content.strip()
    elif isinstance(raw_content, list):
        parts: list[str] = []
        for item in raw_content:
            if isinstance(item, str):
                if item.strip():
                    parts.append(item.strip())
                continue

            item_text = _read_field(item, "text", "")
            if not item_text:
                item_text = _read_field(item, "content", "")
            if not item_text:
                item_text = _read_field(item, "reasoning", "")
            if not item_text:
                item_text = _read_field(item, "refusal", "")
            item_text_str = _stringify_text_value(item_text)
            if not item_text_str and not isinstance(item_text, (str, dict)):
                item_text_str = str(item_text or "").strip()
            if item_text_str:
                parts.append(item_text_str)
        content = "\n".join(parts).strip()
    else:
        content = str(raw_content or "").strip()

    if content:
        return content

    # Some providers place reasoning at choice-level fields.
    choice_reasoning = str(_read_field(choice, "reasoning_content", "") or "").strip()
    if choice_reasoning:
        return choice_reasoning

    choice_reasoning_alt = _read_field(choice, "reasoning", "")
    if isinstance(choice_reasoning_alt, str) and choice_reasoning_alt.strip():
        return choice_reasoning_alt.strip()

    # Some providers place text in message.reasoning or message.output_text.
    message_reasoning = _read_field(message, "reasoning", "")
    if isinstance(message_reasoning, str) and message_reasoning.strip():
        return message_reasoning.strip()
    if isinstance(message_reasoning, list):
        joined = "\n".join(str(item).strip() for item in message_reasoning if str(item).strip()).strip()
        if joined:
            return joined

    output_text = _read_field(message, "output_text", "")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    # Some providers return reasoning text in a separate field.
    reasoning_content = str(_read_field(message, "reasoning_content", "") or "").strip()
    if not reasoning_content:
        model_extra = _read_field(message, "model_extra", {})
        if isinstance(model_extra, dict):
            reasoning_content = str(model_extra.get("reasoning_content", "") or "").strip()
    if reasoning_content:
        return reasoning_content

    refusal_content = str(_read_field(message, "refusal", "") or "").strip()
    if refusal_content:
        return refusal_content

    choice_text = str(_read_field(choice, "text", "") or "").strip()
    if choice_text:
        return choice_text

    tool_calls = _read_field(message, "tool_calls", None) or []
    function_payload: object | None = None
    if tool_calls:
        first_call = tool_calls[0]
        function_payload = _read_field(first_call, "function", None) or first_call
    else:
        # Legacy OpenAI format may return a single function_call field.
        function_payload = _read_field(message, "function_call", None)

    if function_payload is None:
        return ""

    name = str(_read_field(function_payload, "name", "") or "").strip()
    arguments = _coerce_tool_arguments(_read_field(function_payload, "arguments", {}))
    if not name:
        return ""

    payload = {
        "thought": "",
        "function_call": {
            "name": name,
            "arguments": arguments,
        },
    }
    return json.dumps(payload, ensure_ascii=False)


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
        enable_thinking: bool = False,
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
        self._base_url = base_url
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._enable_thinking = enable_thinking

    def _needs_non_stream_qwen_compat(self) -> bool:
        """Return whether non-stream calls should disable Qwen thinking mode.

        Some OpenAI-compatible Qwen endpoints reject non-stream requests unless
        ``enable_thinking`` is explicitly set to ``false``.
        """
        model = self._model.strip().lower()
        base_url = self._base_url.strip().lower()
        return ("qwen" in model) or ("dashscope" in base_url) or ("aliyuncs.com" in base_url)

    def _is_glm_compat(self) -> bool:
        """Return whether current endpoint looks like GLM OpenAI-compatible API."""
        base_url = self._base_url.strip().lower()
        model = self._model.strip().lower()
        return ("open.bigmodel.cn" in base_url) or model.startswith("glm-")

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
            request_kwargs: dict = {
                "model": self._model,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "temperature": temperature if temperature is not None else self._temperature,
                "max_tokens": max_tokens if max_tokens is not None else self._max_tokens,
            }
            if self._needs_non_stream_qwen_compat():
                # Qwen-compatible endpoints require thinking mode disabled for
                # non-stream completions.
                request_kwargs["extra_body"] = {"enable_thinking": False}
            elif self._is_glm_compat():
                # GLM-compatible endpoints support explicit thinking control.
                request_kwargs["extra_body"] = {
                    "thinking": {"type": "enabled" if self._enable_thinking else "disabled"}
                }

            response = await self._client.chat.completions.create(**request_kwargs)
            choice = response.choices[0]
            usage = _read_field(response, "usage", None)
            finish_reason = str(_read_field(choice, "finish_reason", "stop") or "stop")
            model_name = str(_read_field(response, "model", self._model) or self._model)
            prompt_tokens = int(_read_field(usage, "prompt_tokens", 0) or 0) if usage is not None else 0
            completion_tokens = int(_read_field(usage, "completion_tokens", 0) or 0) if usage is not None else 0
            normalized_content = _normalize_openai_message_content(choice)
            if not normalized_content:
                message_obj = _read_field(choice, "message", None)
                logger.warning(
                    "OpenAI completion produced empty normalized content",
                    model=model_name,
                    finish_reason=finish_reason,
                    has_message=message_obj is not None,
                    message_type=type(message_obj).__name__ if message_obj is not None else "none",
                    has_tool_calls=bool(_read_field(message_obj, "tool_calls", None)) if message_obj is not None else False,
                )
            return LLMResponse(
                content=normalized_content,
                model=model_name,
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                finish_reason=finish_reason,
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
            request_kwargs: dict = {
                "model": self._model,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "temperature": temperature if temperature is not None else self._temperature,
                "max_tokens": max_tokens if max_tokens is not None else self._max_tokens,
                "stream": True,
            }
            if self._needs_non_stream_qwen_compat():
                request_kwargs["extra_body"] = {"enable_thinking": self._enable_thinking}
            elif self._is_glm_compat():
                request_kwargs["extra_body"] = {
                    "thinking": {"type": "enabled" if self._enable_thinking else "disabled"}
                }

            stream = await self._client.chat.completions.create(**request_kwargs)
            async for chunk in stream:
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue

                first_choice = choices[0]
                delta_obj = getattr(first_choice, "delta", None)
                delta = getattr(delta_obj, "content", None)
                if delta:
                    yield str(delta)
        except Exception as exc:
            logger.error("OpenAI streaming failed", error=str(exc))
            raise LLMError(f"OpenAI streaming failed: {exc}") from exc
