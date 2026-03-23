"""Tests for remote Skill/MCP installation helpers."""

from __future__ import annotations

import io
import json
from pathlib import Path
import zipfile

import pytest

from deepcode.extensions.mcp_registry import MCPRegistry
from deepcode.extensions.remote_install import (
    get_clawhub_skill_details,
    install_mcp_from_remote,
    install_skill_from_clawhub,
    install_skills_from_remote,
    search_clawhub_skills,
)


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeClient:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        del exc_type, exc, tb

    async def get(self, url: str):
        if url.endswith("skills.json"):
            return _FakeResponse(
                json.dumps(
                    {
                        "skills": [
                            {
                                "name": "python-debug",
                                "description": "Debugging steps",
                                "content": "# Python Debug\nUse logs and pdb.\n",
                            }
                        ]
                    }
                )
            )
        if url.endswith("mcp-servers.json"):
            return _FakeResponse(
                json.dumps(
                    {
                        "servers": [
                            {
                                "name": "docs-index",
                                "transport": "http",
                                "url": "https://example.com/mcp/docs",
                                "description": "Docs retrieval",
                            }
                        ]
                    }
                )
            )
        return _FakeResponse("{}", status_code=404)


@pytest.mark.asyncio
async def test_install_skills_from_remote_manifest(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("deepcode.extensions.remote_install.httpx.AsyncClient", _FakeClient)

    from deepcode.config import Settings

    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr("deepcode.extensions.remote_install.get_settings", lambda: settings)

    result = await install_skills_from_remote("https://skill.example.com/")

    installed = result.get("installed", [])
    assert isinstance(installed, list)
    assert len(installed) == 1
    installed_path = Path(installed[0])
    assert installed_path.exists()
    assert "Python Debug" in installed_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_install_mcp_from_remote_manifest(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("deepcode.extensions.remote_install.httpx.AsyncClient", _FakeClient)

    registry = MCPRegistry(config_path=str(tmp_path / "mcp_servers.json"))
    result = await install_mcp_from_remote("https://mcp.example.com/", registry=registry)

    installed = result.get("installed", [])
    assert isinstance(installed, list)
    assert "docs-index" in installed
    loaded = registry.load()
    assert any(item.name == "docs-index" for item in loaded)


@pytest.mark.asyncio
async def test_search_clawhub_skills_uses_public_api(monkeypatch):
    async def _fake_fetch_json(url: str, timeout: float = 12.0):
        del timeout
        assert url.startswith("https://clawhub.ai/api/v1/search?")
        return {
            "results": [
                {
                    "slug": "agent-browser",
                    "displayName": "Agent Browser",
                    "summary": "Headless browser automation",
                    "version": "2.0.0",
                    "updatedAt": 1730000000000,
                    "score": 0.98,
                }
            ]
        }

    monkeypatch.setattr("deepcode.extensions.remote_install._fetch_json", _fake_fetch_json)

    rows = await search_clawhub_skills("https://clawhub.ai", query="browser", limit=5)

    assert len(rows) == 1
    assert rows[0]["slug"] == "agent-browser"
    assert rows[0]["download_url"] == "https://clawhub.ai/api/v1/download?slug=agent-browser"


@pytest.mark.asyncio
async def test_get_clawhub_skill_details_loads_skill_markdown(monkeypatch):
    async def _fake_fetch_json(url: str, timeout: float = 12.0):
        del timeout
        assert url == "https://clawhub.ai/api/v1/skills/agent-browser"
        return {
            "skill": {
                "slug": "agent-browser",
                "displayName": "Agent Browser",
                "summary": "Automate the web",
                "stats": {"downloads": 1200},
            },
            "latestVersion": {"version": "1.4.0", "license": "MIT-0"},
            "owner": {"handle": "TheSethRose", "displayName": "Seth"},
            "metadata": {"os": ["linux"]},
            "moderation": {"isSuspicious": False},
            "security": {"verdict": "clean"},
        }

    async def _fake_fetch_text(url: str, timeout: float = 12.0):
        del timeout
        assert url.startswith("https://clawhub.ai/api/v1/skills/agent-browser/file?path=")
        return "# Agent Browser\nUse browser automation.\n"

    monkeypatch.setattr("deepcode.extensions.remote_install._fetch_json", _fake_fetch_json)
    monkeypatch.setattr("deepcode.extensions.remote_install._fetch_text", _fake_fetch_text)

    result = await get_clawhub_skill_details("https://clawhub.ai", slug="agent-browser")

    assert result["name"] == "Agent Browser"
    assert result["owner_handle"] == "TheSethRose"
    assert result["skill_markdown"].startswith("# Agent Browser")


@pytest.mark.asyncio
async def test_install_skill_from_clawhub_downloads_archive(monkeypatch, tmp_path: Path):
    archive_path = tmp_path / "agent-browser.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("agent-browser/SKILL.md", "# Agent Browser\n")

    async def _fake_details(source_url: str, *, slug: str):
        assert source_url == "https://clawhub.ai"
        assert slug == "agent-browser"
        return {
            "api_url": "https://clawhub.ai/api/v1/skills/agent-browser",
            "web_url": "https://clawhub.ai/TheSethRose/agent-browser",
            "version": "1.4.0",
        }

    async def _fake_fetch_bytes(url: str, timeout: float = 20.0):
        del timeout
        assert url == "https://clawhub.ai/api/v1/download?slug=agent-browser"
        return archive_path.read_bytes()

    from deepcode.config import Settings

    monkeypatch.setattr("deepcode.extensions.remote_install.get_clawhub_skill_details", _fake_details)
    monkeypatch.setattr("deepcode.extensions.remote_install._fetch_bytes", _fake_fetch_bytes)
    monkeypatch.setattr("deepcode.extensions.remote_install.get_settings", lambda: Settings(data_dir=tmp_path))

    result = await install_skill_from_clawhub("https://clawhub.ai", slug="agent-browser")

    assert result["provider"] == "clawhub"
    assert result["package_name"] == "agent-browser-1.4.0"
    assert Path(result["install_dir"]).exists()
    download_cache = tmp_path / "skills" / "_downloads"
    assert not download_cache.exists() or not any(download_cache.iterdir())


@pytest.mark.asyncio
async def test_search_clawhub_skills_falls_back_to_html_on_429(monkeypatch):
    async def _rate_limited(url: str, timeout: float = 12.0):
        del url, timeout
        raise RuntimeError("Rate limited by remote source (HTTP 429) for https://clawhub.ai/api/v1/search")

    async def _fake_fetch_text(url: str, timeout: float = 12.0):
        del timeout
        assert url.startswith("https://clawhub.ai/search?q=browser")
        return '<a href="/TheSethRose/agent-browser">Agent Browser</a>'

    monkeypatch.setattr("deepcode.extensions.remote_install._fetch_json", _rate_limited)
    monkeypatch.setattr("deepcode.extensions.remote_install._fetch_text", _fake_fetch_text)

    rows = await search_clawhub_skills("https://clawhub.ai", query="browser", limit=5)

    assert len(rows) == 1
    assert rows[0]["slug"] == "agent-browser"


@pytest.mark.asyncio
async def test_get_clawhub_skill_details_falls_back_to_html_on_429(monkeypatch):
    async def _rate_limited(url: str, timeout: float = 12.0):
        del url, timeout
        raise RuntimeError("Rate limited by remote source (HTTP 429) for https://clawhub.ai/api/v1/skills/agent-browser")

    async def _fake_fetch_text(url: str, timeout: float = 12.0):
        del timeout
        if url == "https://clawhub.ai/skills/agent-browser":
            raise RuntimeError("http 404")
        if url == "https://clawhub.ai/search?q=agent-browser":
            return '<a href="/TheSethRose/agent-browser">Agent Browser</a>'
        if url == "https://clawhub.ai/TheSethRose/agent-browser":
            return (
                "<html><head>"
                "<title>Agent Browser - ClawHub</title>"
                '<meta name="description" content="Headless browser automation">'
                "</head><body>"
                '<a href="https://cdn.clawhub.ai/agent-browser.zip">Download</a>'
                "</body></html>"
            )
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("deepcode.extensions.remote_install._fetch_json", _rate_limited)
    monkeypatch.setattr("deepcode.extensions.remote_install._fetch_text", _fake_fetch_text)

    details = await get_clawhub_skill_details("https://clawhub.ai", slug="agent-browser")

    assert details["slug"] == "agent-browser"
    assert details["name"] == "Agent Browser"
    assert details["summary"] == "Headless browser automation"
    assert details["download_url"] == "https://cdn.clawhub.ai/agent-browser.zip"


@pytest.mark.asyncio
async def test_install_skill_from_clawhub_falls_back_to_html_download_url(monkeypatch, tmp_path: Path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("agent-browser/SKILL.md", "# Agent Browser\n")
    archive_bytes = buffer.getvalue()
    fetched_urls: list[str] = []

    async def _fake_details(source_url: str, *, slug: str):
        assert source_url == "https://clawhub.ai"
        assert slug == "agent-browser"
        return {
            "api_url": "https://clawhub.ai/api/v1/skills/agent-browser",
            "web_url": "https://clawhub.ai/TheSethRose/agent-browser",
            "version": "1.4.0",
            "download_url": "https://clawhub.ai/api/v1/download?slug=agent-browser",
        }

    async def _fake_fetch_bytes(url: str, timeout: float = 20.0):
        del timeout
        fetched_urls.append(url)
        if "api/v1/download" in url:
            raise RuntimeError("Rate limited by remote source (HTTP 429) for https://clawhub.ai/api/v1/download")
        if url == "https://cdn.clawhub.ai/agent-browser.zip":
            return archive_bytes
        raise AssertionError(f"unexpected url: {url}")

    async def _fake_fetch_text(url: str, timeout: float = 12.0):
        del timeout
        if url == "https://clawhub.ai/skills/agent-browser":
            return '<a href="https://cdn.clawhub.ai/agent-browser.zip">Download</a>'
        raise AssertionError(f"unexpected url: {url}")

    from deepcode.config import Settings

    monkeypatch.setattr("deepcode.extensions.remote_install.get_clawhub_skill_details", _fake_details)
    monkeypatch.setattr("deepcode.extensions.remote_install._fetch_bytes", _fake_fetch_bytes)
    monkeypatch.setattr("deepcode.extensions.remote_install._fetch_text", _fake_fetch_text)
    monkeypatch.setattr("deepcode.extensions.remote_install.get_settings", lambda: Settings(data_dir=tmp_path))

    result = await install_skill_from_clawhub("https://clawhub.ai", slug="agent-browser")

    assert result["provider"] == "clawhub"
    assert Path(result["install_dir"]).exists()
    assert fetched_urls[0] == "https://clawhub.ai/api/v1/download?slug=agent-browser"
    assert fetched_urls[-1] == "https://cdn.clawhub.ai/agent-browser.zip"


@pytest.mark.asyncio
async def test_install_skills_from_remote_clawhub_detail_url(monkeypatch):
    async def _fake_install(source_url: str, *, slug: str, version: str = "", tag: str = ""):
        del version, tag
        assert source_url == "https://clawhub.ai/TheSethRose/agent-browser"
        assert slug == "agent-browser"
        return {
            "install_dir": "/tmp/skills/agent-browser",
            "package_name": "agent-browser",
        }

    monkeypatch.setattr("deepcode.extensions.remote_install.install_skill_from_clawhub", _fake_install)

    result = await install_skills_from_remote("https://clawhub.ai/TheSethRose/agent-browser")

    assert result["provider"] == "clawhub"
    assert result["installed"] == ["/tmp/skills/agent-browser"]


@pytest.mark.asyncio
async def test_fetch_text_retries_on_429(monkeypatch):
    attempts = {"count": 0}

    class _RetryClient:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb

        async def get(self, url: str):
            attempts["count"] += 1
            if attempts["count"] == 1:
                response = _FakeResponse("rate limited", status_code=429)
                response.headers = {"retry-after": "0"}
                return response
            return _FakeResponse('{"ok": true}')

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("deepcode.extensions.remote_install.httpx.AsyncClient", _RetryClient)
    monkeypatch.setattr("deepcode.extensions.remote_install.asyncio.sleep", _no_sleep)

    payload = await search_clawhub_skills("https://clawhub.ai", query="browser", limit=5)

    assert attempts["count"] >= 2
    assert payload == []

