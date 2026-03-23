"""Shell command execution tool with an allow-list safety guard."""

from __future__ import annotations

import asyncio
import os
import shlex
import tempfile
from typing import Any

from deepcode.config import get_settings
from deepcode.logging_config import get_logger
from deepcode.tools.base import BaseTool, ToolResult

logger = get_logger(__name__)


def _windows_creationflags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(asyncio.subprocess, "CREATE_NO_WINDOW", 0))


class ShellTool(BaseTool):
    """Execute whitelisted shell commands in an isolated subprocess.

    Only the first token of a command (the binary name) is checked against the
    allow-list defined by ``DEEPCODE_ALLOWED_SHELLS``. This prevents obvious
    abuse while still allowing safe inspection commands.
    """

    _SAFE_BASELINE_COMMANDS = {
        "ls",
        "cat",
        "grep",
        "find",
        "python3",
        "pip",
        "echo",
        "pwd",
        "head",
        "tail",
        "wc",
        "ping",
        "nslookup",
        "tracert",
        "curl",
    }

    @property
    def name(self) -> str:
        return "shell"

    @classmethod
    def _allowed_commands(cls) -> list[str]:
        configured = {command.lower() for command in get_settings().allowed_shell_commands}
        merged = configured | cls._SAFE_BASELINE_COMMANDS
        return sorted(merged)

    @property
    def description(self) -> str:
        allowed = ", ".join(self._allowed_commands())
        return (
            "Run a whitelisted shell command and return its output. "
            f"Allowed commands: {allowed}."
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
        binary_name = binary.split("/")[-1].split("\\")[-1].lower()
        for suffix in (".exe", ".cmd", ".bat"):
            if binary_name.endswith(suffix):
                binary_name = binary_name[: -len(suffix)]
                break

        # Normalize path-prefixed binaries to command name for portability.
        # Example: /bin/echo test -> echo test
        normalized_tokens = [binary_name] + tokens[1:]
        if os.name == "nt" and binary_name == "ls":
            normalized_tokens = [
                tempfile.gettempdir() if token == "/tmp" else token
                for token in normalized_tokens
            ]

        allowed = self._allowed_commands()
        if binary_name not in allowed:
            return ToolResult.fail(
                self.name,
                f"Command '{binary_name}' is not allowed. "
                f"Allowed commands: {', '.join(sorted(allowed))}",
            )

        logger.debug("Running shell command", command=command, timeout=timeout)

        try:
            if os.name == "nt":
                # Use PowerShell for consistent aliases like ls/echo on Windows.
                ps_command = " ".join(shlex.quote(token) for token in normalized_tokens)
                proc = await asyncio.create_subprocess_exec(
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    ps_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=_windows_creationflags(),
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    *normalized_tokens,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=_windows_creationflags(),
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
