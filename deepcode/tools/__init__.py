"""Tool suite for DeepCode Agent."""

from __future__ import annotations

from pathlib import Path

from deepcode.tools.base import BaseTool, ToolResult
from deepcode.tools.code_executor import CodeExecutorTool
from deepcode.tools.extension_tools import MCPServiceTool, SkillRegistryTool
from deepcode.tools.file_manager import FileManagerTool
from deepcode.tools.script_runner import ScriptRunnerTool
from deepcode.tools.shell_tool import ShellTool
from deepcode.tools.web_browser import WebBrowserTool


def build_default_tools(root: Path | str | None = None) -> list[BaseTool]:
    """Build the default tool bundle with coding + MCP/Skill capabilities."""
    return [
        CodeExecutorTool(),
        FileManagerTool(root=root),
        ScriptRunnerTool(root=root),
        ShellTool(),
        MCPServiceTool(),
        SkillRegistryTool(),
        WebBrowserTool(),
    ]

__all__ = [
    "BaseTool",
    "ToolResult",
    "CodeExecutorTool",
    "FileManagerTool",
    "ScriptRunnerTool",
    "ShellTool",
    "MCPServiceTool",
    "SkillRegistryTool",
    "WebBrowserTool",
    "build_default_tools",
]
