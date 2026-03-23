"""Shared pytest fixtures for DeepCode Agent tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from deepcode.llm.mock_client import MockLLMClient
from deepcode.tools import CodeExecutorTool, FileManagerTool, ScriptRunnerTool, ShellTool


@pytest.fixture(scope="session")
def event_loop_policy():
    """Use the default event loop policy."""
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
def mock_llm() -> MockLLMClient:
    """Return a MockLLMClient with no pre-configured responses."""
    return MockLLMClient()


@pytest.fixture
def mock_llm_with_responses() -> MockLLMClient:
    """Return a MockLLMClient with a simple final-answer response."""
    response = (
        '```json\n'
        '{"thought": "I can answer this", '
        '"action": "final_answer", '
        '"action_input": {"answer": "Mock answer"}}\n'
        '```'
    )
    return MockLLMClient(responses=[response])


@pytest.fixture
def code_executor() -> CodeExecutorTool:
    """Return a CodeExecutorTool instance."""
    return CodeExecutorTool()


@pytest.fixture
def shell_tool() -> ShellTool:
    """Return a ShellTool instance."""
    return ShellTool()


@pytest.fixture
def file_manager(tmp_path: Path) -> FileManagerTool:
    """Return a FileManagerTool rooted at a temporary directory."""
    return FileManagerTool(root=tmp_path)


@pytest.fixture
def script_runner(tmp_path: Path) -> ScriptRunnerTool:
    """Return a ScriptRunnerTool rooted at a temporary directory."""
    return ScriptRunnerTool(root=tmp_path)


@pytest.fixture
def tmp_session_db(tmp_path: Path) -> str:
    """Return a temporary SQLite database URL for session store tests."""
    return f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
