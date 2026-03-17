"""Unit tests for the tool suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from deepcode.tools.base import ToolResult
from deepcode.tools.code_executor import CodeExecutorTool
from deepcode.tools.file_manager import FileManagerTool
from deepcode.tools.shell_tool import ShellTool


class TestToolResult:
    def test_ok_factory(self):
        result = ToolResult.ok("test_tool", "some output", key="value")
        assert result.success is True
        assert result.output == "some output"
        assert result.metadata["key"] == "value"
        assert result.tool_name == "test_tool"

    def test_fail_factory(self):
        result = ToolResult.fail("test_tool", "something went wrong")
        assert result.success is False
        assert result.error == "something went wrong"
        assert result.output == ""


class TestCodeExecutorTool:
    @pytest.mark.asyncio
    async def test_name_and_description(self, code_executor: CodeExecutorTool):
        assert code_executor.name == "code_executor"
        assert len(code_executor.description) > 10

    @pytest.mark.asyncio
    async def test_executes_simple_expression(self, code_executor: CodeExecutorTool):
        result = await code_executor.run(code="print(1 + 1)")
        assert result.success is True
        assert "2" in result.output

    @pytest.mark.asyncio
    async def test_captures_stdout(self, code_executor: CodeExecutorTool):
        result = await code_executor.run(code="print('hello deepcode')")
        assert result.success is True
        assert "hello deepcode" in result.output

    @pytest.mark.asyncio
    async def test_captures_stderr_on_error(self, code_executor: CodeExecutorTool):
        result = await code_executor.run(code="raise ValueError('test error')")
        assert result.success is False
        assert "ValueError" in result.error or "test error" in result.error

    @pytest.mark.asyncio
    async def test_timeout_kills_long_running_code(self, code_executor: CodeExecutorTool):
        result = await code_executor.run(code="import time; time.sleep(60)", timeout=1)
        assert result.success is False
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    async def test_handles_syntax_error(self, code_executor: CodeExecutorTool):
        result = await code_executor.run(code="def broken(: pass")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_multiline_code(self, code_executor: CodeExecutorTool):
        code = """
def add(a, b):
    return a + b

print(add(3, 4))
"""
        result = await code_executor.run(code=code)
        assert result.success is True
        assert "7" in result.output


class TestShellTool:
    @pytest.mark.asyncio
    async def test_name_and_description(self, shell_tool: ShellTool):
        assert shell_tool.name == "shell"

    @pytest.mark.asyncio
    async def test_echo_command(self, shell_tool: ShellTool):
        result = await shell_tool.run(command="echo hello")
        assert result.success is True
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_ls_command(self, shell_tool: ShellTool):
        result = await shell_tool.run(command="ls /tmp")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_disallowed_command_is_blocked(self, shell_tool: ShellTool):
        result = await shell_tool.run(command="rm -rf /")
        assert result.success is False
        assert "not allowed" in result.error.lower()

    @pytest.mark.asyncio
    async def test_path_prefixed_binary_is_normalised(self, shell_tool: ShellTool):
        # /bin/echo should normalise to "echo" which is allowed
        result = await shell_tool.run(command="/bin/echo test")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_empty_command_fails(self, shell_tool: ShellTool):
        result = await shell_tool.run(command="   ")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_timeout(self, shell_tool: ShellTool):
        result = await shell_tool.run(command="echo slow", timeout=5)
        assert result.success is True


class TestFileManagerTool:
    @pytest.mark.asyncio
    async def test_name_and_description(self, file_manager: FileManagerTool):
        assert file_manager.name == "file_manager"

    @pytest.mark.asyncio
    async def test_write_and_read(self, file_manager: FileManagerTool):
        write = await file_manager.run(action="write", path="test.txt", content="hello")
        assert write.success is True

        read = await file_manager.run(action="read", path="test.txt")
        assert read.success is True
        assert read.output == "hello"

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, file_manager: FileManagerTool):
        result = await file_manager.run(action="read", path="does_not_exist.txt")
        assert result.success is False
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_list_directory(self, file_manager: FileManagerTool, tmp_path: Path):
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.py").write_text("y")
        result = await file_manager.run(action="list", path=".")
        assert result.success is True
        assert "a.py" in result.output
        assert "b.py" in result.output

    @pytest.mark.asyncio
    async def test_tree_action(self, file_manager: FileManagerTool, tmp_path: Path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("pass")
        result = await file_manager.run(action="tree", path=".")
        assert result.success is True
        assert "src" in result.output

    @pytest.mark.asyncio
    async def test_exists_action(self, file_manager: FileManagerTool, tmp_path: Path):
        (tmp_path / "exists.txt").write_text("yes")
        result_yes = await file_manager.run(action="exists", path="exists.txt")
        result_no = await file_manager.run(action="exists", path="no.txt")

        assert result_yes.success is True
        assert result_yes.metadata["exists"] is True
        assert result_no.metadata["exists"] is False

    @pytest.mark.asyncio
    async def test_path_traversal_is_blocked(self, file_manager: FileManagerTool):
        result = await file_manager.run(action="read", path="../../etc/passwd")
        assert result.success is False
        assert "outside" in result.error.lower() or "denied" in result.error.lower()

    @pytest.mark.asyncio
    async def test_unknown_action_fails(self, file_manager: FileManagerTool):
        result = await file_manager.run(action="hack")
        assert result.success is False
        assert "unknown action" in result.error.lower()

    @pytest.mark.asyncio
    async def test_write_creates_parent_directories(self, file_manager: FileManagerTool):
        result = await file_manager.run(
            action="write",
            path="subdir/nested/file.py",
            content="pass",
        )
        assert result.success is True
        read = await file_manager.run(action="read", path="subdir/nested/file.py")
        assert read.output == "pass"
