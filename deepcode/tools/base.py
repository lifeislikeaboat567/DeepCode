"""Base classes and data models for tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """Result returned by a tool invocation."""

    tool_name: str = Field(description="Name of the tool that produced this result")
    success: bool = Field(description="Whether the tool executed successfully")
    output: str = Field(default="", description="Tool output (stdout/result)")
    error: str = Field(default="", description="Error message if the tool failed")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata from the tool execution",
    )

    @classmethod
    def ok(cls, tool_name: str, output: str, **metadata: Any) -> ToolResult:
        """Create a successful tool result."""
        return cls(tool_name=tool_name, success=True, output=output, metadata=metadata)

    @classmethod
    def fail(cls, tool_name: str, error: str, **metadata: Any) -> ToolResult:
        """Create a failed tool result."""
        return cls(tool_name=tool_name, success=False, error=error, metadata=metadata)


class BaseTool(ABC):
    """Abstract base class for all DeepCode tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool identifier used in agent prompts."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what the tool does."""

    @abstractmethod
    async def run(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with the given arguments.

        Args:
            **kwargs: Tool-specific arguments.

        Returns:
            A :class:`ToolResult` describing the outcome.
        """
