"""Custom exception types for DeepCode Agent."""

from __future__ import annotations


class DeepCodeError(Exception):
    """Base exception for all DeepCode errors."""


class ConfigurationError(DeepCodeError):
    """Raised when required configuration is missing or invalid."""


class LLMError(DeepCodeError):
    """Raised when the LLM backend returns an error or is unavailable."""


class ExecutionError(DeepCodeError):
    """Raised when code or shell execution fails or times out."""


class ExecutionTimeoutError(ExecutionError):
    """Raised when code execution exceeds the allowed time limit."""


class FileManagerError(DeepCodeError):
    """Raised for file system operation errors."""


class MemoryError(DeepCodeError):
    """Raised when memory read/write operations fail."""


class SessionNotFoundError(DeepCodeError):
    """Raised when a requested session does not exist."""


class TaskNotFoundError(DeepCodeError):
    """Raised when a requested task does not exist."""


class ToolError(DeepCodeError):
    """Raised when a tool invocation fails."""
