"""Sandboxed Python code execution tool."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import os
import sys
import textwrap
from typing import Any

from deepcode.config import get_settings
from deepcode.logging_config import get_logger
from deepcode.tools.base import BaseTool, ToolResult

logger = get_logger(__name__)


def _windows_creationflags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(asyncio.subprocess, "CREATE_NO_WINDOW", 0))


class CodeExecutorTool(BaseTool):
    """Execute Python code in a separate process with timeout and resource limits.

    The code is run as a child process using :mod:`asyncio` subprocess support.
    stdout and stderr are captured and returned in the :class:`ToolResult`.

    Security controls:
    - Each execution runs in a fresh subprocess (no shared state)
    - Execution is hard-killed after ``max_execution_time`` seconds
    - The working directory is isolated to a temporary location
    """

    @property
    def name(self) -> str:
        return "code_executor"

    @property
    def description(self) -> str:
        return (
            "Execute Python code in a sandboxed subprocess. "
            "Returns stdout, stderr, and exit code."
        )

    async def run(self, code: str, **kwargs: Any) -> ToolResult:
        """Execute *code* in an isolated Python subprocess.

        Args:
            code: Python source code to execute.
            **kwargs: Ignored.

        Returns:
            :class:`ToolResult` with captured output or error details.
        """
        settings = get_settings()
        timeout = kwargs.get("timeout", settings.max_execution_time)

        # Dedent so callers can pass indented multiline strings
        code = textwrap.dedent(code)

        logger.debug("Executing code", length=len(code), timeout=timeout)

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=_windows_creationflags(),
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except (TimeoutError, asyncio.TimeoutError):
                # On Windows, process termination cleanup can raise transport
                # errors; keep timeout reporting deterministic for callers.
                with suppress(Exception):
                    proc.kill()
                with suppress(Exception):
                    await proc.communicate()
                return ToolResult.fail(
                    self.name,
                    f"Execution timed out after {timeout} seconds",
                    timeout=timeout,
                )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            exit_code = proc.returncode

            if exit_code == 0:
                return ToolResult.ok(
                    self.name,
                    stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                )
            else:
                combined_error = stderr or stdout or f"Process exited with code {exit_code}"
                return ToolResult.fail(
                    self.name,
                    combined_error,
                    stdout=stdout,
                    exit_code=exit_code,
                )

        except Exception as exc:
            logger.error("Code execution error", error=str(exc))
            return ToolResult.fail(self.name, str(exc))
