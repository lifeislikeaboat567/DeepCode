"""Hook manager for lifecycle extension points."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Union

HookCallback = Callable[["HookContext"], Union[Awaitable[None], None]]


class HookEvent(str, Enum):
    """Lifecycle hook events exposed by DeepCode agents."""

    BEFORE_LLM = "before_llm"
    AFTER_LLM = "after_llm"
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    TASK_STARTED = "task_started"
    TASK_FINISHED = "task_finished"


@dataclass
class HookContext:
    """Data payload provided to hook callbacks."""

    event: HookEvent
    task: str = ""
    iteration: int = 0
    action: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


class HookManager:
    """Register and emit lifecycle hooks."""

    def __init__(self) -> None:
        self._hooks: dict[HookEvent, list[HookCallback]] = {event: [] for event in HookEvent}

    def register(self, event: HookEvent, callback: HookCallback) -> None:
        """Register callback for a specific lifecycle event."""
        self._hooks[event].append(callback)

    async def emit(self, context: HookContext) -> None:
        """Emit an event to all registered callbacks."""
        for callback in self._hooks.get(context.event, []):
            maybe = callback(context)
            if asyncio.iscoroutine(maybe):
                await maybe

    def stats(self) -> dict[str, int]:
        """Return number of registered callbacks per event."""
        return {event.value: len(callbacks) for event, callbacks in self._hooks.items()}
