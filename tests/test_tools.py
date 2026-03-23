"""Unit tests for the tool suite."""

from __future__ import annotations

from pathlib import Path
import json
import os
import zipfile

import pytest

from deepcode.extensions import MCPRegistry, MCPServerConfig, SkillRegistry, SkillToggleStore, install_skill_archive_bytes
from deepcode.tools.base import ToolResult
from deepcode.tools.code_executor import CodeExecutorTool
from deepcode.tools.extension_tools import MCPServiceTool, SkillRegistryTool
from deepcode.tools.file_manager import FileManagerTool
from deepcode.tools.script_runner import ScriptRunnerTool
from deepcode.tools.shell_tool import ShellTool
from deepcode.tools.web_browser import WebBrowserTool


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

    @pytest.mark.asyncio
    async def test_ping_command_is_allowed(self, shell_tool: ShellTool):
        command = "ping -n 1 127.0.0.1" if os.name == "nt" else "ping -c 1 127.0.0.1"
        result = await shell_tool.run(command=command)
        assert "not allowed" not in result.error.lower()


class TestScriptRunnerTool:
    @pytest.mark.asyncio
    async def test_name_and_description(self, script_runner: ScriptRunnerTool):
        assert script_runner.name == "script_runner"
        assert "script" in script_runner.description.lower()

    @pytest.mark.asyncio
    async def test_write_without_execute(self, script_runner: ScriptRunnerTool):
        result = await script_runner.run(
            path="scripts/sample.py",
            content="print('saved only')",
            execute=False,
        )
        assert result.success is True
        assert Path(result.metadata["path"]).exists()

    @pytest.mark.asyncio
    async def test_write_and_execute_python(self, script_runner: ScriptRunnerTool):
        result = await script_runner.run(
            path="scripts/run_me.py",
            content="print('deepcode script ok')",
            execute=True,
        )
        assert result.success is True
        assert "deepcode script ok" in result.output

    @pytest.mark.asyncio
    async def test_unsupported_language_fails(self, script_runner: ScriptRunnerTool):
        result = await script_runner.run(
            path="scripts/run_me.js",
            content="console.log('x')",
            language="javascript",
        )
        assert result.success is False
        assert "unsupported script language" in result.error.lower()


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

    @pytest.mark.asyncio
    async def test_share_action_returns_markdown_link(self, file_manager: FileManagerTool):
        await file_manager.run(action="write", path="exports/report.txt", content="hello")
        result = await file_manager.run(action="share", path="exports/report.txt")

        assert result.success is True
        assert "Attachment ready:" in result.output
        assert "[report.txt](file:///" in result.output

    @pytest.mark.asyncio
    async def test_send_alias_maps_to_share_action(self, file_manager: FileManagerTool):
        await file_manager.run(action="write", path="exports/data.csv", content="id,name\n1,a")
        result = await file_manager.run(action="send", path="exports/data.csv", name="导出数据")

        assert result.success is True
        assert "[导出数据](file:///" in result.output

    @pytest.mark.asyncio
    async def test_send_file_alias_maps_to_share_action(self, file_manager: FileManagerTool):
        await file_manager.run(action="write", path="exports/image.png", content="fake")
        result = await file_manager.run(action="send_file", path="exports/image.png")

        assert result.success is True
        assert "[image.png](file:///" in result.output

    @pytest.mark.asyncio
    async def test_write_action_missing_required_args_returns_validation_error(self, file_manager: FileManagerTool):
        result = await file_manager.run(action="write")

        assert result.success is False
        assert "missing required argument" in result.error.lower()
        assert "path" in result.error.lower()
        assert "content" in result.error.lower()

    @pytest.mark.asyncio
    async def test_share_action_missing_path_returns_validation_error(self, file_manager: FileManagerTool):
        result = await file_manager.run(action="share")

        assert result.success is False
        assert "missing required argument" in result.error.lower()
        assert "path" in result.error.lower()


class TestSkillRegistryTool:
    @pytest.mark.asyncio
    async def test_list_read_and_search(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "python_debug.md").write_text(
            "# Python Debugging\nUse breakpoints. #debug #python\n",
            encoding="utf-8",
        )

        tool = SkillRegistryTool(registry=SkillRegistry(skills_dir=str(skills_dir)))

        listed = await tool.run(action="list", query="debug")
        assert listed.success is True
        listed_data = json.loads(listed.output)
        assert len(listed_data) == 1
        assert listed_data[0]["name"] == "python_debug"

        read = await tool.run(action="read", name="python_debug")
        assert read.success is True
        assert "Python Debugging" in read.output

        searched = await tool.run(action="search", query="breakpoints")
        assert searched.success is True
        searched_data = json.loads(searched.output)
        assert searched_data
        assert searched_data[0]["name"] == "python_debug"

    @pytest.mark.asyncio
    async def test_disabled_skill_is_not_exposed_by_default(self, tmp_path: Path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        disabled_path = skills_dir / "private_ops.md"
        disabled_path.write_text("# Private Ops\ninternal only\n", encoding="utf-8")

        toggle_store = SkillToggleStore(file_path=str(tmp_path / "skill_toggles.json"))
        toggle_store.set_enabled(str(disabled_path), False)

        tool = SkillRegistryTool(
            registry=SkillRegistry(skills_dir=str(skills_dir)),
            status_store=toggle_store,
        )

        listed = await tool.run(action="list")
        assert listed.success is True
        assert json.loads(listed.output) == []

        read_blocked = await tool.run(action="read", name="private_ops")
        assert read_blocked.success is False
        assert "disabled" in read_blocked.error.lower()

        read_allowed = await tool.run(action="read", name="private_ops", enabled_only=False)
        assert read_allowed.success is True
        assert "Private Ops" in read_allowed.output

    def test_packaged_skill_archive_install_and_discover(self, tmp_path: Path):
        archive_path = tmp_path / "demo-skill-1.2.0.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("demo-skill-1.2.0/SKILL.md", "# Demo Skill\nPackaged install. #demo\n")
            archive.writestr("demo-skill-1.2.0/assets/example.txt", "ok")

        install_result = install_skill_archive_bytes(
            archive_path.name,
            archive_path.read_bytes(),
            skills_dir=tmp_path / "skills",
        )

        assert install_result["package_name"] == "demo-skill-1.2.0"

        discovered = SkillRegistry(skills_dir=str(tmp_path / "skills")).discover()
        assert len(discovered) == 1
        assert discovered[0].name == "demo-skill-1.2.0"
        assert discovered[0].description == "Demo Skill"

    def test_skill_archive_without_manifest_is_rejected(self, tmp_path: Path):
        archive_path = tmp_path / "broken-skill.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("broken-skill/README.md", "missing manifest")

        with pytest.raises(ValueError, match="SKILL.md"):
            install_skill_archive_bytes(
                archive_path.name,
                archive_path.read_bytes(),
                skills_dir=tmp_path / "skills",
            )

    @pytest.mark.asyncio
    async def test_install_archive_action(self, tmp_path: Path):
        archive_path = tmp_path / "ops-pack-2.0.0.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("ops-pack-2.0.0/SKILL.md", "# Ops Pack\n")

        tool = SkillRegistryTool(registry=SkillRegistry(skills_dir=str(tmp_path / "skills")))
        installed = await tool.run(action="install_archive", path=str(archive_path))

        assert installed.success is True
        payload = json.loads(installed.output)
        assert payload["package_name"] == "ops-pack-2.0.0"

    @pytest.mark.asyncio
    async def test_install_archive_from_url_action(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        buffer_path = tmp_path / "remote-pack.zip"
        with zipfile.ZipFile(buffer_path, "w") as archive:
            archive.writestr("remote-pack/SKILL.md", "# Remote Pack\n")

        tool = SkillRegistryTool(registry=SkillRegistry(skills_dir=str(tmp_path / "skills")))

        class _FakeResponse:
            def __init__(self):
                self.url = "https://example.test/files/remote-pack.zip"
                self.content = buffer_path.read_bytes()

            def raise_for_status(self):
                return None

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url: str):
                assert url == "https://example.test/files/remote-pack.zip"
                return _FakeResponse()

        monkeypatch.setattr("deepcode.tools.extension_tools.httpx.AsyncClient", _FakeClient)

        installed = await tool.run(action="install_archive", url="https://example.test/files/remote-pack.zip")
        assert installed.success is True
        payload = json.loads(installed.output)
        assert payload["package_name"] == "remote-pack"

    @pytest.mark.asyncio
    async def test_search_clawhub_action(self, monkeypatch: pytest.MonkeyPatch):
        async def _fake_search(source_url: str, **kwargs: object):
            assert source_url == "https://clawhub.ai"
            assert kwargs["query"] == "browser"
            return [
                {
                    "provider": "clawhub",
                    "slug": "agent-browser",
                    "name": "Agent Browser",
                    "summary": "Automation skill",
                }
            ]

        monkeypatch.setattr("deepcode.tools.extension_tools.search_clawhub_skills", _fake_search)

        tool = SkillRegistryTool()
        result = await tool.run(action="search_clawhub", source_url="https://clawhub.ai", query="browser")

        assert result.success is True
        payload = json.loads(result.output)
        assert payload[0]["slug"] == "agent-browser"

    @pytest.mark.asyncio
    async def test_read_clawhub_action(self, monkeypatch: pytest.MonkeyPatch):
        async def _fake_details(source_url: str, *, slug: str):
            assert source_url == "https://clawhub.ai"
            assert slug == "agent-browser"
            return {
                "provider": "clawhub",
                "slug": slug,
                "name": "Agent Browser",
                "version": "1.2.0",
                "skill_markdown": "# Agent Browser\n",
            }

        monkeypatch.setattr("deepcode.tools.extension_tools.get_clawhub_skill_details", _fake_details)

        tool = SkillRegistryTool()
        result = await tool.run(action="read_clawhub", slug="agent-browser")

        assert result.success is True
        payload = json.loads(result.output)
        assert payload["skill_markdown"].startswith("# Agent Browser")

    @pytest.mark.asyncio
    async def test_install_clawhub_action(self, monkeypatch: pytest.MonkeyPatch):
        async def _fake_install(source_url: str, *, slug: str, version: str = "", tag: str = ""):
            assert source_url == "https://clawhub.ai"
            assert slug == "agent-browser"
            assert version == ""
            assert tag == "latest"
            return {
                "provider": "clawhub",
                "slug": slug,
                "package_name": "agent-browser-1.0.0",
                "install_dir": "/tmp/agent-browser-1.0.0",
            }

        monkeypatch.setattr("deepcode.tools.extension_tools.install_skill_from_clawhub", _fake_install)

        tool = SkillRegistryTool()
        result = await tool.run(action="install_clawhub", slug="agent-browser", tag="latest")

        assert result.success is True
        payload = json.loads(result.output)
        assert payload["package_name"] == "agent-browser-1.0.0"

    @pytest.mark.asyncio
    async def test_auto_install_clawhub_action_selects_best_candidate(self, monkeypatch: pytest.MonkeyPatch):
        async def _fake_search(source_url: str, **kwargs: object):
            assert source_url == "https://clawhub.ai"
            assert kwargs["query"] == "browser"
            return [
                {"slug": "starter-browser", "name": "Starter Browser", "score": 0.2},
                {"slug": "agent-browser", "name": "Agent Browser", "score": 0.9},
            ]

        async def _fake_details(source_url: str, *, slug: str):
            assert source_url == "https://clawhub.ai"
            assert slug == "agent-browser"
            return {
                "name": "Agent Browser",
                "summary": "Headless browser",
                "version": "2.1.0",
            }

        async def _fake_install(source_url: str, *, slug: str, version: str = "", tag: str = ""):
            assert source_url == "https://clawhub.ai"
            assert slug == "agent-browser"
            assert version == ""
            assert tag == ""
            return {
                "provider": "clawhub",
                "slug": slug,
                "package_name": "agent-browser-2.1.0",
                "install_dir": "/tmp/skills/agent-browser-2.1.0",
            }

        monkeypatch.setattr("deepcode.tools.extension_tools.search_clawhub_skills", _fake_search)
        monkeypatch.setattr("deepcode.tools.extension_tools.get_clawhub_skill_details", _fake_details)
        monkeypatch.setattr("deepcode.tools.extension_tools.install_skill_from_clawhub", _fake_install)

        tool = SkillRegistryTool()
        result = await tool.run(action="auto_install_clawhub", query="browser")

        assert result.success is True
        payload = json.loads(result.output)
        assert payload["mode"] == "query"
        assert payload["selected"]["slug"] == "agent-browser"
        assert payload["installation"]["package_name"] == "agent-browser-2.1.0"

    @pytest.mark.asyncio
    async def test_auto_install_clawhub_action_dry_run(self, monkeypatch: pytest.MonkeyPatch):
        async def _fake_search(source_url: str, **kwargs: object):
            assert source_url == "https://clawhub.ai"
            assert kwargs["query"] == "agent-browser"
            return [{"slug": "agent-browser", "name": "Agent Browser", "score": 0.7}]

        async def _fake_details(source_url: str, *, slug: str):
            assert source_url == "https://clawhub.ai"
            assert slug == "agent-browser"
            return {
                "name": "Agent Browser",
                "summary": "Headless browser",
                "version": "2.1.0",
            }

        async def _should_not_install(*args, **kwargs):
            raise AssertionError("install should not be called in dry_run mode")

        monkeypatch.setattr("deepcode.tools.extension_tools.search_clawhub_skills", _fake_search)
        monkeypatch.setattr("deepcode.tools.extension_tools.get_clawhub_skill_details", _fake_details)
        monkeypatch.setattr("deepcode.tools.extension_tools.install_skill_from_clawhub", _should_not_install)

        tool = SkillRegistryTool()
        result = await tool.run(action="auto_install_clawhub", query="agent-browser", dry_run=True)

        assert result.success is True
        payload = json.loads(result.output)
        assert payload["mode"] == "dry_run"
        assert payload["selected"]["slug"] == "agent-browser"


class TestMCPServiceTool:
    @pytest.mark.asyncio
    async def test_list_and_describe(self, tmp_path: Path):
        registry = MCPRegistry(config_path=str(tmp_path / "mcp_servers.json"))
        registry.upsert(
            MCPServerConfig(
                name="demo-http",
                transport="http",
                command="https://example.test/api",
                enabled=True,
                description="demo",
            )
        )
        tool = MCPServiceTool(registry=registry)

        listed = await tool.run(action="list_servers")
        assert listed.success is True
        listed_data = json.loads(listed.output)
        assert listed_data[0]["name"] == "demo-http"

        described = await tool.run(action="describe_server", name="demo-http")
        assert described.success is True
        described_data = json.loads(described.output)
        assert described_data["transport"] == "http"

    @pytest.mark.asyncio
    async def test_request_http(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        registry = MCPRegistry(config_path=str(tmp_path / "mcp_servers.json"))
        registry.upsert(
            MCPServerConfig(
                name="demo-http",
                transport="http",
                command="https://example.test/api",
                enabled=True,
            )
        )
        tool = MCPServiceTool(registry=registry)

        class _FakeResponse:
            status_code = 200
            reason_phrase = "OK"
            headers = {"content-type": "application/json"}

            @property
            def text(self) -> str:
                return '{"ok": true}'

            def json(self):
                return {"ok": True}

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def request(self, method: str, url: str, **kwargs):
                assert method == "POST"
                assert "example.test" in url
                return _FakeResponse()

        monkeypatch.setattr("deepcode.tools.extension_tools.httpx.AsyncClient", _FakeClient)

        result = await tool.run(
            action="request",
            name="demo-http",
            method="POST",
            path="/invoke",
            payload={"tool": "status"},
        )
        assert result.success is True
        assert "HTTP 200" in result.output
        assert '"ok": true' in result.output

    @pytest.mark.asyncio
    async def test_request_rejects_stdio(self, tmp_path: Path):
        registry = MCPRegistry(config_path=str(tmp_path / "mcp_servers.json"))
        registry.upsert(
            MCPServerConfig(
                name="demo-stdio",
                transport="stdio",
                command="python mcp_server.py",
                enabled=True,
            )
        )
        tool = MCPServiceTool(registry=registry)

        result = await tool.run(action="request", name="demo-stdio")
        assert result.success is False
        assert "http/sse" in result.error.lower()


class TestWebBrowserTool:
    @pytest.mark.asyncio
    async def test_fetch_page(self, monkeypatch: pytest.MonkeyPatch):
        tool = WebBrowserTool()

        class _FakeResponse:
            status_code = 200
            headers = {"content-type": "text/html; charset=utf-8"}
            url = "https://example.test/skills"
            text = "<html><head><title>Skill Hub</title></head><body><a href='/a.zip'>A</a><p>Useful skills</p></body></html>"

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url: str):
                assert url == "https://example.test/skills"
                return _FakeResponse()

        monkeypatch.setattr("deepcode.tools.web_browser.httpx.AsyncClient", _FakeClient)

        result = await tool.run(action="fetch", url="https://example.test/skills")
        assert result.success is True
        payload = json.loads(result.output)
        assert payload["title"] == "Skill Hub"
        assert "Useful skills" in payload["text"]
        assert payload["links"][0] == "https://example.test/a.zip"
