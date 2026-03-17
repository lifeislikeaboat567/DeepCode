"""Shell command execution tool with an allow-list safety guard."""

from __future__ import annotations

import asyncio
import shlex
from typing import Any

from deepcode.config import get_settings
from deepcode.logging_config import get_logger
from deepcode.tools.base import BaseTool, ToolResult

logger = get_logger(__name__)


class ShellTool(BaseTool):
    """Execute whitelisted shell commands in an isolated subprocess.

    Only the first token of a command (the binary name) is checked against the
    allow-list defined by ``DEEPCODE_ALLOWED_SHELLS``. This prevents obvious
    abuse while still allowing safe inspection commands.
    """

    @property
    def name(self) -> str:
        return "shell"

    @property
    def description(self) -> str:
        return (
            "Run a whitelisted shell command and return its output. "
            "Allowed commands: ls, cat, grep, find, python3, pip, echo, pwd, head, tail, wc."
        )

    async def run(self, command: str, **kwargs: Any) -> ToolResult:
        """Execute *command* if its binary is on the allow-list.

        Args:
            command: Shell command string to execute.
            **kwargs: Ignored.

        Returns:
            :class:`ToolResult` with captured stdout/stderr.
        """
        settings = get_settings()
        timeout = kwargs.get("timeout", settings.max_execution_time)

        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            return ToolResult.fail(self.name, f"Invalid command syntax: {exc}")

        if not tokens:
            return ToolResult.fail(self.name, "Empty command")

        binary = tokens[0]
        # Strip any path component so "/bin/ls" → "ls"
        binary_name = binary.split("/")[-1]

        allowed = settings.allowed_shell_commands
        if binary_name not in allowed:
            return ToolResult.fail(
                self.name,
                f"Command '{binary_name}' is not allowed. "
                f"Allowed commands: {', '.join(sorted(allowed))}",
            )

        logger.debug("Running shell command", command=command, timeout=timeout)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                return ToolResult.fail(
                    self.name,
                    f"Command timed out after {timeout} seconds",
                    timeout=timeout,
                )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            exit_code = proc.returncode

            if exit_code == 0:
                return ToolResult.ok(self.name, stdout, stderr=stderr, exit_code=exit_code)
            else:
                combined_error = stderr or stdout or f"Exited with code {exit_code}"
                return ToolResult.fail(
                    self.name,
                    combined_error,
                    stdout=stdout,
                    exit_code=exit_code,
                )

        except Exception as exc:
            logger.error("Shell execution error", error=str(exc))
            return ToolResult.fail(self.name, str(exc))
