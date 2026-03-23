"""In-memory short-term conversation history manager."""

from __future__ import annotations

from collections import deque
from typing import Any

from pydantic import BaseModel, Field

from deepcode.config import get_settings
from deepcode.llm.base import LLMMessage


class ConversationTurn(BaseModel):
    """A single exchange in a conversation."""

    role: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ShortTermMemory:
    """Manages a rolling window of conversation messages.

    Messages are stored as :class:`LLMMessage` objects. When the window
    exceeds ``max_messages``, the oldest non-system messages are discarded
    to stay within the configured token budget.

    Args:
        max_messages: Maximum number of messages to retain (default from settings).
        system_prompt: Optional persistent system prompt prepended to all
            message lists returned by :meth:`get_messages`.
    """

    def __init__(
        self,
        max_messages: int | None = None,
        system_prompt: str | None = None,
    ) -> None:
        settings = get_settings()
        self._max = max_messages or settings.max_history_messages
        self._messages: deque[LLMMessage] = deque()
        self._system_prompt = system_prompt

    def add(self, role: str, content: str) -> None:
        """Append a message to the conversation history.

        Args:
            role: One of ``"user"``, ``"assistant"``, or ``"system"``.
            content: Message text.
        """
        self._messages.append(LLMMessage(role=role, content=content))
        self._trim()

    def add_message(self, message: LLMMessage) -> None:
        """Append a pre-built :class:`LLMMessage`.

        Args:
            message: The message to append.
        """
        self._messages.append(message)
        self._trim()

    def get_messages(self, include_system: bool = True) -> list[LLMMessage]:
        """Return a copy of the conversation history.

        Args:
            include_system: When ``True`` (default), prepend the system
                prompt if one was configured.

        Returns:
            Ordered list of :class:`LLMMessage` objects.
        """
        history = list(self._messages)
        if include_system and self._system_prompt:
            return [LLMMessage.system(self._system_prompt)] + history
        return history

    def clear(self) -> None:
        """Remove all messages from the history (preserves system prompt)."""
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)

    def _trim(self) -> None:
        """Discard the oldest non-system messages if over the limit."""
        while len(self._messages) > self._max:
            self._messages.popleft()
