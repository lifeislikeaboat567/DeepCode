"""Tools for MCP service access and local skill discovery."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urljoin

import httpx

from deepcode.extensions import (
    MCPRegistry,
    SkillRegistry,
    SkillToggleStore,
    get_clawhub_skill_details,
    install_skill_archive_bytes,
    install_skill_from_clawhub,
    resolve_clawhub_skill_slug,
    search_clawhub_skills,
)
from deepcode.tools.base import BaseTool, ToolResult


def _parse_bool(value: object, *, default: bool) -> bool:
    """Convert incoming value to bool while supporting common string forms."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "enabled", "on"}:
            return True
        if normalized in {"0", "false", "no", "disabled", "off"}:
            return False
    return default


def _parse_headers(value: object) -> dict[str, str]:
    """Parse request headers from dict or JSON string."""
    if isinstance(value, dict):
        return {str(key): str(val) for key, val in value.items()}
    if isinstance(value, str) and value.strip():
        raw = json.loads(value)
        if isinstance(raw, dict):
            return {str(key): str(val) for key, val in raw.items()}
    return {}


def _parse_payload(value: object) -> object:
    """Parse payload from object or JSON string."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return value
    return value


class MCPServiceTool(BaseTool):
    """Inspect configured MCP servers and query HTTP/SSE endpoints."""

    def __init__(
        self,
        registry: MCPRegistry | None = None,
        *,
        max_output_chars: int = 10000,
    ) -> None:
        self._registry = registry or MCPRegistry()
        self._max_output_chars = max_output_chars

    @property
    def name(self) -> str:
        return "mcp_service"

    @property
    def description(self) -> str:
        return (
            "Inspect configured MCP servers and call HTTP/SSE MCP endpoints. "
            "Actions: list_servers, describe_server, request."
        )

    async def run(self, action: str, **kwargs: object) -> ToolResult:
        dispatch = {
            "list_servers": self._list_servers,
            "describe_server": self._describe_server,
            "request": self._request,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult.fail(
                self.name,
                "Unknown action. Use one of: list_servers, describe_server, request.",
            )

        try:
            return await handler(**kwargs)
        except Exception as exc:
            return ToolResult.fail(self.name, f"MCP action failed: {exc}")

    async def _list_servers(self, **kwargs: object) -> ToolResult:
        enabled_only = _parse_bool(kwargs.get("enabled_only"), default=True)
        rows = []
        for server in self._registry.load():
            if enabled_only and not server.enabled:
                continue
            rows.append(
                {
                    "name": server.name,
                    "transport": server.transport,
                    "command": server.command,
                    "enabled": server.enabled,
                    "description": server.description,
                }
            )
        return ToolResult.ok(
            self.name,
            json.dumps(rows, ensure_ascii=False, indent=2),
            count=len(rows),
        )

    async def _describe_server(self, **kwargs: object) -> ToolResult:
        name = str(kwargs.get("name", "")).strip()
        if not name:
            return ToolResult.fail(self.name, "Parameter 'name' is required.")

        for server in self._registry.load():
            if server.name != name:
                continue
            return ToolResult.ok(
                self.name,
                json.dumps(server.model_dump(), ensure_ascii=False, indent=2),
                name=server.name,
                transport=server.transport,
                enabled=server.enabled,
            )

        return ToolResult.fail(self.name, f"MCP server '{name}' not found.")

    async def _request(self, **kwargs: object) -> ToolResult:
        name = str(kwargs.get("name", "")).strip()
        if not name:
            return ToolResult.fail(self.name, "Parameter 'name' is required.")

        server = next((item for item in self._registry.load() if item.name == name), None)
        if server is None:
            return ToolResult.fail(self.name, f"MCP server '{name}' not found.")
        if not server.enabled:
            return ToolResult.fail(self.name, f"MCP server '{name}' is disabled.")
        if server.transport not in {"http", "sse"}:
            return ToolResult.fail(
                self.name,
                "Only HTTP/SSE MCP servers are supported by this tool.",
                transport=server.transport,
            )

        base = server.command.strip()
        if not (base.startswith("http://") or base.startswith("https://")):
            return ToolResult.fail(
                self.name,
                "Server command must be an http(s) URL for action=request.",
                command=base,
            )

        path = str(kwargs.get("path", "")).strip()
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        elif path:
            url = urljoin(base.rstrip("/") + "/", path.lstrip("/"))
        else:
            url = base

        method = str(kwargs.get("method", "GET")).strip().upper() or "GET"
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            return ToolResult.fail(self.name, f"Unsupported method '{method}'.")

        timeout_raw = kwargs.get("timeout", 20)
        try:
            timeout = max(1.0, float(timeout_raw))
        except (TypeError, ValueError):
            timeout = 20.0

        headers = _parse_headers(kwargs.get("headers"))
        payload = _parse_payload(kwargs.get("payload"))

        request_kwargs: dict[str, object] = {}
        if headers:
            request_kwargs["headers"] = headers
        if method == "GET":
            if isinstance(payload, dict):
                request_kwargs["params"] = payload
        else:
            if isinstance(payload, (dict, list)):
                request_kwargs["json"] = payload
            elif payload is not None:
                request_kwargs["content"] = str(payload)

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.request(method, url, **request_kwargs)
        except httpx.HTTPError as exc:
            return ToolResult.fail(self.name, f"HTTP request failed: {exc}", url=url, method=method)

        content_type = response.headers.get("content-type", "").lower()
        if "application/json" in content_type:
            try:
                body = json.dumps(response.json(), ensure_ascii=False, indent=2)
            except ValueError:
                body = response.text
        else:
            body = response.text

        truncated = False
        if len(body) > self._max_output_chars:
            body = body[: self._max_output_chars] + "\n...(truncated)"
            truncated = True

        output = f"HTTP {response.status_code} {response.reason_phrase}\nURL: {url}\n\n{body}"
        if response.status_code >= 400:
            return ToolResult.fail(
                self.name,
                output,
                status_code=response.status_code,
                url=url,
                method=method,
                truncated=truncated,
            )

        return ToolResult.ok(
            self.name,
            output,
            status_code=response.status_code,
            url=url,
            method=method,
            truncated=truncated,
        )


class SkillRegistryTool(BaseTool):
    """Discover and read local markdown skills for prompt-time reuse."""

    def __init__(
        self,
        registry: SkillRegistry | None = None,
        status_store: SkillToggleStore | None = None,
        *,
        max_read_chars: int = 15000,
    ) -> None:
        self._registry = registry or SkillRegistry()
        self._status_store = status_store or SkillToggleStore()
        self._max_read_chars = max_read_chars

    @property
    def name(self) -> str:
        return "skill_registry"

    @property
    def description(self) -> str:
        return (
            "Discover and inspect enabled local markdown skills. "
            "Actions: list, search, read, install_archive, search_clawhub, read_clawhub, install_clawhub, auto_install_clawhub."
        )

    def _discovered_skills(self, *, enabled_only: bool) -> list[tuple[object, bool]]:
        rows: list[tuple[object, bool]] = []
        for skill in self._registry.discover():
            enabled = self._status_store.is_enabled(str(skill.path), default=True)
            if enabled_only and not enabled:
                continue
            rows.append((skill, enabled))
        return rows

    async def run(self, action: str, **kwargs: object) -> ToolResult:
        dispatch = {
            "list": self._list,
            "search": self._search,
            "read": self._read,
            "install_archive": self._install_archive,
            "search_clawhub": self._search_clawhub,
            "read_clawhub": self._read_clawhub,
            "install_clawhub": self._install_clawhub,
            "auto_install_clawhub": self._auto_install_clawhub,
        }
        handler = dispatch.get(action)
        if handler is None:
            return ToolResult.fail(
                self.name,
                "Unknown action. Use one of: list, search, read, install_archive, search_clawhub, read_clawhub, install_clawhub, auto_install_clawhub.",
            )

        try:
            return await handler(**kwargs)
        except Exception as exc:
            return ToolResult.fail(self.name, f"Skill action failed: {exc}")

    async def _list(self, **kwargs: object) -> ToolResult:
        query = str(kwargs.get("query", "")).strip().lower()
        tag = str(kwargs.get("tag", "")).strip().lower()
        enabled_only = _parse_bool(kwargs.get("enabled_only"), default=True)
        limit_raw = kwargs.get("limit", 50)
        try:
            limit = max(1, int(limit_raw))
        except (TypeError, ValueError):
            limit = 50

        rows = []
        for skill, enabled in self._discovered_skills(enabled_only=enabled_only):
            if tag and tag not in skill.tags:
                continue
            searchable = " ".join([skill.name, skill.description, " ".join(skill.tags)]).lower()
            if query and query not in searchable:
                continue
            rows.append(
                {
                    "name": skill.name,
                    "path": skill.path,
                    "description": skill.description,
                    "tags": skill.tags,
                    "enabled": enabled,
                }
            )
            if len(rows) >= limit:
                break

        return ToolResult.ok(
            self.name,
            json.dumps(rows, ensure_ascii=False, indent=2),
            count=len(rows),
        )

    async def _search(self, **kwargs: object) -> ToolResult:
        query = str(kwargs.get("query", "")).strip().lower()
        if not query:
            return ToolResult.fail(self.name, "Parameter 'query' is required.")

        enabled_only = _parse_bool(kwargs.get("enabled_only"), default=True)
        limit_raw = kwargs.get("limit", 10)
        context_raw = kwargs.get("context_lines", 2)
        try:
            limit = max(1, int(limit_raw))
        except (TypeError, ValueError):
            limit = 10
        try:
            context_lines = max(0, int(context_raw))
        except (TypeError, ValueError):
            context_lines = 2

        matches = []
        for skill, _enabled in self._discovered_skills(enabled_only=enabled_only):
            path = Path(skill.path)
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue

            for line_no, line in enumerate(lines, start=1):
                if query not in line.lower():
                    continue

                start = max(0, line_no - 1 - context_lines)
                end = min(len(lines), line_no + context_lines)
                snippet = "\n".join(lines[start:end])
                matches.append(
                    {
                        "name": skill.name,
                        "path": skill.path,
                        "line": line_no,
                        "snippet": snippet,
                    }
                )
                if len(matches) >= limit:
                    return ToolResult.ok(
                        self.name,
                        json.dumps(matches, ensure_ascii=False, indent=2),
                        count=len(matches),
                    )

        return ToolResult.ok(
            self.name,
            json.dumps(matches, ensure_ascii=False, indent=2),
            count=len(matches),
        )

    async def _read(self, **kwargs: object) -> ToolResult:
        name = str(kwargs.get("name", "")).strip()
        path_arg = str(kwargs.get("path", "")).strip()
        enabled_only = _parse_bool(kwargs.get("enabled_only"), default=True)

        discovered = self._discovered_skills(enabled_only=False)
        target: tuple[object, bool] | None = None

        if path_arg:
            candidate = Path(path_arg)
            for skill, enabled in discovered:
                try:
                    same = Path(skill.path).resolve() == candidate.resolve()
                except OSError:
                    same = Path(skill.path) == candidate
                if same:
                    target = (skill, enabled)
                    break

        if target is None and name:
            for skill, enabled in discovered:
                if skill.name == name or skill.name.lower() == name.lower():
                    target = (skill, enabled)
                    break

        if target is None:
            return ToolResult.fail(self.name, "Skill not found. Provide 'name' or valid 'path'.")

        skill, enabled = target
        if enabled_only and not enabled:
            return ToolResult.fail(self.name, f"Skill '{skill.name}' is disabled.")

        target_path = Path(skill.path)

        try:
            text = target_path.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult.fail(self.name, f"Unable to read skill file: {exc}")

        truncated = False
        if len(text) > self._max_read_chars:
            text = text[: self._max_read_chars] + "\n...(truncated)"
            truncated = True

        return ToolResult.ok(
            self.name,
            text,
            path=str(target_path),
            enabled=enabled,
            truncated=truncated,
        )

    async def _install_archive(self, **kwargs: object) -> ToolResult:
        archive_path = str(kwargs.get("path", "")).strip()
        archive_url = str(kwargs.get("url", "")).strip()

        archive_name = ""
        archive_bytes: bytes | None = None

        if archive_path:
            target = Path(archive_path)
            if not target.exists() or not target.is_file():
                return ToolResult.fail(self.name, f"Archive not found: {archive_path}")
            archive_name = target.name
            archive_bytes = target.read_bytes()
        elif archive_url:
            if not (archive_url.startswith("http://") or archive_url.startswith("https://")):
                return ToolResult.fail(self.name, "Parameter 'url' must be a valid http(s) URL.")
            try:
                async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                    response = await client.get(archive_url)
                    response.raise_for_status()
            except httpx.HTTPError as exc:
                return ToolResult.fail(self.name, f"Archive download failed: {exc}", url=archive_url)
            archive_name = Path(str(response.url).split("?", 1)[0]).name or "skill.zip"
            archive_bytes = response.content
        else:
            return ToolResult.fail(self.name, "Parameter 'path' or 'url' is required.")

        try:
            skills_dir = getattr(self._registry, "_skills_dir", None)
            result = install_skill_archive_bytes(archive_name, archive_bytes, skills_dir=skills_dir)
        except Exception as exc:
            return ToolResult.fail(self.name, str(exc), path=archive_path, url=archive_url)

        return ToolResult.ok(
            self.name,
            json.dumps(result, ensure_ascii=False, indent=2),
            package_name=str(result.get("package_name", "")),
            install_dir=str(result.get("install_dir", "")),
        )

    async def _search_clawhub(self, **kwargs: object) -> ToolResult:
        source_url = str(kwargs.get("source_url", "https://clawhub.ai")).strip() or "https://clawhub.ai"
        query = str(kwargs.get("query", "")).strip()
        limit_raw = kwargs.get("limit", 10)
        try:
            limit = max(1, min(int(limit_raw), 50))
        except (TypeError, ValueError):
            limit = 10

        rows = await search_clawhub_skills(
            source_url,
            query=query,
            limit=limit,
            non_suspicious_only=_parse_bool(kwargs.get("non_suspicious_only"), default=True),
            highlighted_only=_parse_bool(kwargs.get("highlighted_only"), default=False),
        )
        return ToolResult.ok(
            self.name,
            json.dumps(rows, ensure_ascii=False, indent=2),
            provider="clawhub",
            count=len(rows),
        )

    async def _read_clawhub(self, **kwargs: object) -> ToolResult:
        source_url = str(kwargs.get("source_url", "https://clawhub.ai")).strip() or "https://clawhub.ai"
        slug = resolve_clawhub_skill_slug(str(kwargs.get("slug", "")).strip() or str(kwargs.get("url", "")).strip())
        if not slug:
            return ToolResult.fail(self.name, "Parameter 'slug' or ClawHub skill 'url' is required.")

        details = await get_clawhub_skill_details(source_url, slug=slug)
        return ToolResult.ok(
            self.name,
            json.dumps(details, ensure_ascii=False, indent=2),
            provider="clawhub",
            slug=slug,
            version=str(details.get("version", "")),
        )

    async def _install_clawhub(self, **kwargs: object) -> ToolResult:
        source_url = str(kwargs.get("source_url", "https://clawhub.ai")).strip() or "https://clawhub.ai"
        slug = resolve_clawhub_skill_slug(str(kwargs.get("slug", "")).strip() or str(kwargs.get("url", "")).strip())
        if not slug:
            return ToolResult.fail(self.name, "Parameter 'slug' or ClawHub skill 'url' is required.")

        result = await install_skill_from_clawhub(
            source_url,
            slug=slug,
            version=str(kwargs.get("version", "")).strip(),
            tag=str(kwargs.get("tag", "")).strip(),
        )
        return ToolResult.ok(
            self.name,
            json.dumps(result, ensure_ascii=False, indent=2),
            provider="clawhub",
            slug=slug,
            install_dir=str(result.get("install_dir", "")),
            package_name=str(result.get("package_name", "")),
        )

    async def _auto_install_clawhub(self, **kwargs: object) -> ToolResult:
        source_url = str(kwargs.get("source_url", "https://clawhub.ai")).strip() or "https://clawhub.ai"
        explicit_slug = resolve_clawhub_skill_slug(
            str(kwargs.get("slug", "")).strip() or str(kwargs.get("url", "")).strip()
        )
        query = str(kwargs.get("query", "")).strip()
        dry_run = _parse_bool(kwargs.get("dry_run"), default=False)

        version = str(kwargs.get("version", "")).strip()
        tag = str(kwargs.get("tag", "")).strip()

        if explicit_slug:
            details = await get_clawhub_skill_details(source_url, slug=explicit_slug)
            if dry_run:
                preview = {
                    "provider": "clawhub",
                    "mode": "dry_run",
                    "selected": {
                        "slug": explicit_slug,
                        "name": str(details.get("name", "")),
                        "summary": str(details.get("summary", "")),
                        "version": str(details.get("version", "")),
                    },
                }
                return ToolResult.ok(
                    self.name,
                    json.dumps(preview, ensure_ascii=False, indent=2),
                    provider="clawhub",
                    slug=explicit_slug,
                    dry_run=True,
                )

            result = await install_skill_from_clawhub(source_url, slug=explicit_slug, version=version, tag=tag)
            response = {
                "provider": "clawhub",
                "mode": "slug",
                "selected": {
                    "slug": explicit_slug,
                    "name": str(details.get("name", "")),
                    "summary": str(details.get("summary", "")),
                    "version": str(details.get("version", "")),
                },
                "installation": result,
            }
            return ToolResult.ok(
                self.name,
                json.dumps(response, ensure_ascii=False, indent=2),
                provider="clawhub",
                slug=explicit_slug,
                install_dir=str(result.get("install_dir", "")),
                package_name=str(result.get("package_name", "")),
            )

        if not query:
            return ToolResult.fail(self.name, "Parameter 'query' is required when 'slug' or 'url' is not provided.")

        limit_raw = kwargs.get("limit", 8)
        try:
            limit = max(1, min(int(limit_raw), 50))
        except (TypeError, ValueError):
            limit = 8

        candidates = await search_clawhub_skills(
            source_url,
            query=query,
            limit=limit,
            non_suspicious_only=_parse_bool(kwargs.get("non_suspicious_only"), default=True),
            highlighted_only=_parse_bool(kwargs.get("highlighted_only"), default=False),
        )
        if not candidates:
            return ToolResult.fail(self.name, f"No ClawHub skills found for query '{query}'.")

        query_slug = resolve_clawhub_skill_slug(query)
        selected: dict[str, object] | None = None
        selected_rank: tuple[int, float] | None = None
        for row in candidates:
            row_slug = resolve_clawhub_skill_slug(str(row.get("slug", "")))
            is_exact = 1 if query_slug and row_slug == query_slug else 0
            try:
                score = float(row.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            rank = (is_exact, score)
            if selected is None or (selected_rank is not None and rank > selected_rank):
                selected = row
                selected_rank = rank

        if selected is None:
            selected = candidates[0]

        selected_slug = resolve_clawhub_skill_slug(str(selected.get("slug", "")))
        if not selected_slug:
            return ToolResult.fail(self.name, "Unable to resolve selected ClawHub skill slug.")

        details = await get_clawhub_skill_details(source_url, slug=selected_slug)
        if dry_run:
            preview = {
                "provider": "clawhub",
                "mode": "dry_run",
                "query": query,
                "candidate_count": len(candidates),
                "selected": {
                    "slug": selected_slug,
                    "name": str(details.get("name", selected.get("name", ""))),
                    "summary": str(details.get("summary", selected.get("summary", ""))),
                    "version": str(details.get("version", selected.get("version", ""))),
                    "score": selected.get("score"),
                },
            }
            return ToolResult.ok(
                self.name,
                json.dumps(preview, ensure_ascii=False, indent=2),
                provider="clawhub",
                query=query,
                slug=selected_slug,
                dry_run=True,
            )

        result = await install_skill_from_clawhub(source_url, slug=selected_slug, version=version, tag=tag)
        response = {
            "provider": "clawhub",
            "mode": "query",
            "query": query,
            "candidate_count": len(candidates),
            "selected": {
                "slug": selected_slug,
                "name": str(details.get("name", selected.get("name", ""))),
                "summary": str(details.get("summary", selected.get("summary", ""))),
                "version": str(details.get("version", selected.get("version", ""))),
                "score": selected.get("score"),
            },
            "installation": result,
        }
        return ToolResult.ok(
            self.name,
            json.dumps(response, ensure_ascii=False, indent=2),
            provider="clawhub",
            query=query,
            slug=selected_slug,
            install_dir=str(result.get("install_dir", "")),
            package_name=str(result.get("package_name", "")),
        )