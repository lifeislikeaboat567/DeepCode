"""File system management tool for reading, writing and scanning files."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from deepcode.exceptions import FileManagerError
from deepcode.logging_config import get_logger
from deepcode.tools.base import BaseTool, ToolResult

logger = get_logger(__name__)

# Maximum file size to read/write (10 MB)
_MAX_FILE_SIZE = 10 * 1024 * 1024

# File extensions considered as text
_TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".json",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".md", ".rst", ".txt",
    ".sh", ".bash", ".zsh", ".env", ".sql", ".xml", ".csv",
}


class FileManagerTool(BaseTool):
    """Read, write, and inspect files within a project directory.

    The tool enforces a *root* directory so that agents cannot traverse
    outside the working project.

    Args:
        root: Project root directory. Defaults to the current working directory.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = Path(root or os.getcwd()).resolve()

    @property
    def name(self) -> str:
        return "file_manager"

    @property
    def description(self) -> str:
        return (
            "Read, write, list, and scan files within the project directory. "
            "Actions: read, write, list, tree, exists."
        )

    def _safe_path(self, filepath: str) -> Path:
        """Resolve *filepath* relative to the project root and guard traversal.

        Args:
            filepath: Relative or absolute path string.

        Returns:
            Resolved absolute :class:`~pathlib.Path`.

        Raises:
            FileManagerError: If the resolved path escapes the project root.
        """
        resolved = (self._root / filepath).resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise FileManagerError(
                f"Access denied: '{filepath}' is outside the project root '{self._root}'"
            ) from exc
        return resolved

    async def run(self, action: str, **kwargs: Any) -> ToolResult:
        """Dispatch a file system action.

        Args:
            action: One of ``read``, ``write``, ``list``, ``tree``, ``exists``.
            **kwargs: Action-specific arguments.

        Returns:
            :class:`ToolResult` with the action output.
        """
        dispatch = {
            "read": self._read,
            "write": self._write,
            "list": self._list,
            "tree": self._tree,
            "exists": self._exists,
        }

        if action not in dispatch:
            return ToolResult.fail(
                self.name,
                f"Unknown action '{action}'. Available actions: {', '.join(dispatch)}",
            )

        try:
            return await dispatch[action](**kwargs)
        except FileManagerError as exc:
            return ToolResult.fail(self.name, str(exc))
        except Exception as exc:
            logger.error("File manager error", action=action, error=str(exc))
            return ToolResult.fail(self.name, f"Unexpected error: {exc}")

    async def _read(self, path: str, **_: Any) -> ToolResult:
        """Read the contents of a text file."""
        full_path = self._safe_path(path)
        if not full_path.exists():
            return ToolResult.fail(self.name, f"File not found: '{path}'")
        if not full_path.is_file():
            return ToolResult.fail(self.name, f"Not a file: '{path}'")
        if full_path.stat().st_size > _MAX_FILE_SIZE:
            return ToolResult.fail(self.name, f"File too large (> 10 MB): '{path}'")

        try:
            content = full_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult.fail(self.name, f"File is not valid UTF-8 text: '{path}'")

        return ToolResult.ok(
            self.name,
            content,
            path=str(full_path),
            size=full_path.stat().st_size,
        )

    async def _write(self, path: str, content: str, **_: Any) -> ToolResult:
        """Write *content* to a file, creating parent directories as needed."""
        full_path = self._safe_path(path)
        if len(content.encode("utf-8")) > _MAX_FILE_SIZE:
            return ToolResult.fail(self.name, "Content exceeds 10 MB size limit")

        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        logger.info("File written", path=str(full_path), size=len(content))
        return ToolResult.ok(
            self.name,
            f"Successfully wrote {len(content)} characters to '{path}'",
            path=str(full_path),
        )

    async def _list(self, path: str = ".", **_: Any) -> ToolResult:
        """List the direct contents of a directory."""
        full_path = self._safe_path(path)
        if not full_path.exists():
            return ToolResult.fail(self.name, f"Directory not found: '{path}'")
        if not full_path.is_dir():
            return ToolResult.fail(self.name, f"Not a directory: '{path}'")

        entries = sorted(full_path.iterdir(), key=lambda p: (p.is_file(), p.name))
        lines = []
        for entry in entries:
            indicator = "" if entry.is_dir() else ""
            lines.append(f"{indicator} {entry.name}")

        return ToolResult.ok(self.name, "\n".join(lines), count=len(entries))

    async def _tree(self, path: str = ".", max_depth: int = 3, **_: Any) -> ToolResult:
        """Recursively list the directory tree up to *max_depth* levels."""
        full_path = self._safe_path(path)
        if not full_path.exists():
            return ToolResult.fail(self.name, f"Path not found: '{path}'")

        lines: list[str] = [str(full_path)]
        self._build_tree(full_path, lines, prefix="", depth=0, max_depth=max_depth)
        return ToolResult.ok(self.name, "\n".join(lines))

    def _build_tree(
        self,
        directory: Path,
        lines: list[str],
        prefix: str,
        depth: int,
        max_depth: int,
    ) -> None:
        """Recursively build a tree representation."""
        if depth >= max_depth:
            return

        # Skip hidden and common noise directories
        skip = {"__pycache__", ".git", ".mypy_cache", "node_modules", ".venv", "venv"}
        try:
            entries = sorted(
                (e for e in directory.iterdir() if e.name not in skip),
                key=lambda p: (p.is_file(), p.name),
            )
        except PermissionError:
            return

        for i, entry in enumerate(entries):
            connector = "└── " if i == len(entries) - 1 else "├── "
            lines.append(f"{prefix}{connector}{entry.name}")
            if entry.is_dir():
                extension = "    " if i == len(entries) - 1 else "│   "
                self._build_tree(entry, lines, prefix + extension, depth + 1, max_depth)

    async def _exists(self, path: str, **_: Any) -> ToolResult:
        """Check whether *path* exists."""
        full_path = self._safe_path(path)
        exists = full_path.exists()
        return ToolResult.ok(
            self.name,
            "yes" if exists else "no",
            exists=exists,
            is_file=full_path.is_file() if exists else False,
            is_dir=full_path.is_dir() if exists else False,
        )
