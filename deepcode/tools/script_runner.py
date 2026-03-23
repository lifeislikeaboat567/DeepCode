"""Tool for saving and executing one-off scripts as task artifacts."""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from pathlib import Path
import shutil
import sys
from typing import Any
import uuid

from deepcode.config import get_settings
from deepcode.logging_config import get_logger
from deepcode.tools.base import BaseTool, ToolResult

logger = get_logger(__name__)


def _windows_creationflags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(asyncio.subprocess, "CREATE_NO_WINDOW", 0))


class ScriptRunnerTool(BaseTool):
    """Write and optionally execute a temporary or durable script inside the workspace."""

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = Path(root or os.getcwd()).resolve()

    @property
    def name(self) -> str:
        return "script_runner"

    @property
    def description(self) -> str:
        return (
            "Write and execute one-off scripts. Supports isolated sandboxes and "
            "python/bash/powershell runtimes for deterministic automation tasks."
        )

    def _safe_path(self, path: str) -> Path:
        resolved = (self._root / path).resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise ValueError(f"Access denied outside workspace root: {path}") from exc
        return resolved

    @staticmethod
    def _safe_relative(path: str) -> Path:
        candidate = Path(path)
        if candidate.is_absolute():
            raise ValueError("Isolated mode requires a relative script path")
        if any(part == ".." for part in candidate.parts):
            raise ValueError("Isolated mode does not allow parent path traversal")
        return candidate

    def _resolve_script_path(self, path: str, *, isolated: bool) -> tuple[Path, Path | None]:
        if not isolated:
            return self._safe_path(path), None

        settings = get_settings()
        sandbox_root = (settings.data_dir / "script_sandbox").resolve()
        sandbox_root.mkdir(parents=True, exist_ok=True)
        sandbox_dir = (sandbox_root / uuid.uuid4().hex[:10]).resolve()
        sandbox_dir.mkdir(parents=True, exist_ok=True)

        relative_path = self._safe_relative(path)
        full_path = (sandbox_dir / relative_path).resolve()
        try:
            full_path.relative_to(sandbox_dir)
        except ValueError as exc:
            raise ValueError(f"Access denied in isolated sandbox: {path}") from exc
        return full_path, sandbox_dir

    def _build_process_args(self, language: str, full_path: Path, args: list[str]) -> list[str] | None:
        normalized = language.strip().lower()

        if normalized in {"python", "py"}:
            return [sys.executable, str(full_path), *args]

        if normalized in {"sh", "bash", "shell"}:
            shell_bin = shutil.which("bash") or shutil.which("sh")
            if not shell_bin:
                return None
            return [shell_bin, str(full_path), *args]

        if normalized in {"powershell", "pwsh", "ps1"}:
            ps_bin = shutil.which("pwsh") or shutil.which("powershell")
            if not ps_bin:
                return None
            return [ps_bin, "-File", str(full_path), *args]

        return None

    async def run(
        self,
        path: str,
        content: str,
        language: str = "python",
        args: list[str] | None = None,
        execute: bool = True,
        isolated: bool = True,
        timeout: float | None = None,
        **_: Any,
    ) -> ToolResult:
        settings = get_settings()
        timeout = timeout or settings.max_execution_time
        full_path, sandbox_dir = self._resolve_script_path(path, isolated=bool(isolated))
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

        logger.info("Script written", path=str(full_path), language=language)
        if not execute:
            return ToolResult.ok(
                self.name,
                f"Saved script to '{path}'",
                path=str(full_path),
                language=language,
                isolated=bool(isolated),
                sandbox_dir=str(sandbox_dir) if sandbox_dir else "",
            )

        normalized_language = language.strip().lower()
        run_args = self._build_process_args(normalized_language, full_path, args or [])
        if run_args is None:
            return ToolResult.fail(
                self.name,
                (
                    f"Unsupported script language '{language}'. Supported: "
                    "python, bash/sh, powershell."
                ),
                path=str(full_path),
            )

        working_dir = str(sandbox_dir or self._root)
        try:
            proc = await asyncio.create_subprocess_exec(
                *run_args,
                cwd=working_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=_windows_creationflags(),
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except (TimeoutError, asyncio.TimeoutError):
                with suppress(Exception):
                    proc.kill()
                with suppress(Exception):
                    await proc.communicate()
                return ToolResult.fail(
                    self.name,
                    f"Script timed out after {timeout} seconds",
                    path=str(full_path),
                    timeout=timeout,
                    isolated=bool(isolated),
                    sandbox_dir=str(sandbox_dir) if sandbox_dir else "",
                )
        except Exception as exc:
            logger.error("Script execution error", path=str(full_path), error=str(exc))
            return ToolResult.fail(self.name, str(exc), path=str(full_path))

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        exit_code = int(proc.returncode or 0)
        if exit_code == 0:
            return ToolResult.ok(
                self.name,
                stdout,
                path=str(full_path),
                stderr=stderr,
                exit_code=exit_code,
                language=language,
                isolated=bool(isolated),
                sandbox_dir=str(sandbox_dir) if sandbox_dir else "",
            )
        return ToolResult.fail(
            self.name,
            stderr or stdout or f"Script exited with code {exit_code}",
            path=str(full_path),
            stdout=stdout,
            exit_code=exit_code,
            language=language,
            isolated=bool(isolated),
            sandbox_dir=str(sandbox_dir) if sandbox_dir else "",
        )
