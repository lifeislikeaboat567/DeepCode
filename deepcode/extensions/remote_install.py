"""Install Skills and MCP server definitions from remote open-source sources."""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
import re
from pathlib import Path
from urllib.parse import quote, urlencode, urljoin, urlparse

import httpx

from deepcode.config import get_settings
from deepcode.extensions.mcp_registry import MCPRegistry, MCPServerConfig
from deepcode.extensions.skill_archive_installer import install_skill_archive_bytes


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip())
    cleaned = cleaned.strip("-._")
    return cleaned or "item"


def _source_slug(source_url: str) -> str:
    parsed = urlparse(source_url)
    host = _safe_name(parsed.netloc.lower())
    path = _safe_name(parsed.path.strip("/").replace("/", "-"))
    if path:
        return f"{host}-{path}"
    return host or "remote-source"


def _github_repo_from_url(source_url: str) -> tuple[str, str] | None:
    parsed = urlparse(source_url)
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return None
    segments = [part for part in parsed.path.strip("/").split("/") if part]
    if len(segments) < 2:
        return None
    return segments[0], segments[1]


async def _fetch_text(url: str, timeout: float = 12.0) -> str:
    attempts = 3
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(1, attempts + 1):
            response = await client.get(url)
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code == 429:
                headers = getattr(response, "headers", {}) or {}
                retry_after_raw = headers.get("retry-after", "") if isinstance(headers, dict) else ""
                try:
                    retry_after = max(1.0, float(retry_after_raw))
                except (TypeError, ValueError):
                    retry_after = float(attempt)

                if attempt < attempts:
                    await asyncio.sleep(min(retry_after, 4.0))
                    continue

                message = (
                    f"Rate limited by remote source (HTTP 429) for {url}. "
                    "Please wait and retry, or use a direct ClawHub skill URL/slug to bypass search."
                )
                raise RuntimeError(message)

            try:
                response.raise_for_status()
                return response.text
            except Exception as exc:
                last_error = exc
                break

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Unable to fetch remote text from {url}")


async def _fetch_json(url: str, timeout: float = 12.0) -> object:
    text = await _fetch_text(url, timeout=timeout)
    return json.loads(text)


async def _fetch_bytes(url: str, timeout: float = 20.0) -> bytes:
    attempts = 3
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(1, attempts + 1):
            response = await client.get(url)
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code == 429:
                headers = getattr(response, "headers", {}) or {}
                retry_after_raw = headers.get("retry-after", "") if isinstance(headers, dict) else ""
                try:
                    retry_after = max(1.0, float(retry_after_raw))
                except (TypeError, ValueError):
                    retry_after = float(attempt)

                if attempt < attempts:
                    await asyncio.sleep(min(retry_after, 4.0))
                    continue

                message = (
                    f"Rate limited by remote source (HTTP 429) for {url}. "
                    "Please wait and retry, or open the source site and install with an explicit skill URL."
                )
                raise RuntimeError(message)

            try:
                response.raise_for_status()
                return response.content
            except Exception as exc:
                last_error = exc
                break

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Unable to fetch remote bytes from {url}")


def _site_root(source_url: str) -> str:
    parsed = urlparse(source_url)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return source_url.rstrip("/")


def _is_clawhub_source(source_url: str) -> bool:
    host = urlparse(source_url).netloc.lower()
    return host == "clawhub.ai" or host.endswith(".clawhub.ai")


def _clawhub_api_url(source_url: str, route: str, **query: object) -> str:
    base = _site_root(source_url).rstrip("/")
    target = f"{base}/api/v1/{route.lstrip('/')}"
    params = {key: value for key, value in query.items() if value not in {None, ""}}
    if not params:
        return target
    return f"{target}?{urlencode(params)}"


def _is_rate_limited_error(exc: Exception) -> bool:
    text = str(exc)
    return "429" in text or "Rate limited by remote source" in text


def _extract_html_links(source: str) -> list[str]:
    links: list[str] = []
    for match in re.finditer(r"href\s*=\s*['\"]([^'\"]+)['\"]", source, flags=re.IGNORECASE):
        href = html.unescape(str(match.group(1) or "")).strip()
        if href:
            links.append(href)
    return links


def _extract_clawhub_slugs_from_html(source_url: str, html_text: str) -> list[str]:
    slugs: list[str] = []
    seen: set[str] = set()

    for match in re.finditer(r'"slug"\s*:\s*"([^"]+)"', html_text):
        slug = resolve_clawhub_skill_slug(str(match.group(1) or ""))
        if slug and slug not in seen:
            seen.add(slug)
            slugs.append(slug)

    for href in _extract_html_links(html_text):
        absolute = urljoin(_site_root(source_url).rstrip("/") + "/", href)
        slug = resolve_clawhub_skill_slug(absolute)
        if slug and slug not in seen:
            seen.add(slug)
            slugs.append(slug)

    return slugs


def _extract_clawhub_download_url_from_html(source_url: str, slug: str, html_text: str) -> str:
    site_root = _site_root(source_url).rstrip("/")
    normalized_slug = resolve_clawhub_skill_slug(slug)
    fallback_candidates: list[str] = []

    for href in _extract_html_links(html_text):
        absolute = urljoin(site_root + "/", href)
        lowered = absolute.lower()
        if ".zip" in lowered:
            return absolute
        if "/api/v1/download" in lowered and (f"slug={normalized_slug}" in lowered or "slug=" not in lowered):
            fallback_candidates.append(absolute)

    for match in re.finditer(r'"downloadUrl"\s*:\s*"([^"]+)"', html_text):
        candidate = urljoin(site_root + "/", html.unescape(str(match.group(1) or "")))
        if candidate:
            lowered = candidate.lower()
            if ".zip" in lowered:
                return candidate
            if "/api/v1/download" in lowered:
                fallback_candidates.append(candidate)

    if fallback_candidates:
        return fallback_candidates[0]
    return ""


def _extract_html_title(html_text: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    title = html.unescape(match.group(1)).strip()
    title = re.sub(r"\s+[-|·].*$", "", title).strip()
    return title


def _extract_meta_description(html_text: str) -> str:
    match = re.search(
        r"<meta[^>]+name=['\"]description['\"][^>]+content=['\"]([^'\"]+)['\"]",
        html_text,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return html.unescape(str(match.group(1) or "")).strip()


def _extract_version_hint(html_text: str) -> str:
    for pattern in (
        r'"version"\s*:\s*"([0-9]+(?:\.[0-9A-Za-z_-]+)+)"',
        r"version[^0-9]*([0-9]+(?:\.[0-9A-Za-z_-]+)+)",
    ):
        match = re.search(pattern, html_text, flags=re.IGNORECASE)
        if match:
            return str(match.group(1) or "").strip()
    return ""


async def _fetch_clawhub_skill_html_page(source_url: str, slug: str) -> tuple[str, str]:
    base = _site_root(source_url).rstrip("/")
    skill_slug = resolve_clawhub_skill_slug(slug)
    if not skill_slug:
        raise ValueError("A valid ClawHub skill slug is required.")

    candidates: list[str] = []
    parsed_source = urlparse(source_url)
    if parsed_source.scheme in {"http", "https"} and parsed_source.netloc:
        source_slug = resolve_clawhub_skill_slug(source_url)
        if source_slug == skill_slug:
            candidates.append(source_url)

    search_url = f"{base}/search?q={quote(skill_slug)}"
    candidates.append(f"{base}/skills/{skill_slug}")

    checked: set[str] = set()
    for url in candidates:
        normalized = str(url or "").strip()
        if not normalized or normalized in checked:
            continue
        checked.add(normalized)
        try:
            content = await _fetch_text(normalized)
            if content.strip():
                return normalized, content
        except Exception:
            continue

    search_html = await _fetch_text(search_url)
    for href in _extract_html_links(search_html):
        detail_url = urljoin(base + "/", href)
        if resolve_clawhub_skill_slug(detail_url) != skill_slug:
            continue
        try:
            detail_html = await _fetch_text(detail_url)
            if detail_html.strip():
                return detail_url, detail_html
        except Exception:
            continue

    raise RuntimeError(f"Unable to resolve ClawHub skill page for slug '{skill_slug}'")


async def _get_clawhub_skill_details_via_html(source_url: str, slug: str) -> dict[str, object]:
    skill_slug = resolve_clawhub_skill_slug(slug)
    detail_url, detail_html = await _fetch_clawhub_skill_html_page(source_url, skill_slug)
    parsed = urlparse(detail_url)
    segments = [item for item in parsed.path.strip("/").split("/") if item]
    owner_handle = ""
    if len(segments) >= 2 and segments[-1].lower() == skill_slug.lower():
        owner_handle = str(segments[-2]).strip()

    name = _extract_html_title(detail_html) or skill_slug
    summary = _extract_meta_description(detail_html)
    version = _extract_version_hint(detail_html)
    download_url = _extract_clawhub_download_url_from_html(source_url, skill_slug, detail_html)

    return {
        "provider": "clawhub",
        "slug": skill_slug,
        "name": name,
        "summary": summary,
        "owner_handle": owner_handle,
        "owner_name": "",
        "version": version,
        "license": "",
        "updated_at": "",
        "created_at": "",
        "tags": {},
        "stats": {},
        "metadata": {},
        "moderation": {},
        "security": {},
        "skill_markdown": "",
        "download_url": download_url,
        "api_url": "",
        "web_url": detail_url,
    }


async def _resolve_clawhub_download_url_via_html(source_url: str, slug: str) -> str:
    skill_slug = resolve_clawhub_skill_slug(slug)
    _, detail_html = await _fetch_clawhub_skill_html_page(source_url, skill_slug)
    return _extract_clawhub_download_url_from_html(source_url, skill_slug, detail_html)


def _install_skill_archive_with_cleanup(
    archive_name: str,
    archive_bytes: bytes,
    *,
    skills_dir: Path,
) -> dict[str, object]:
    temp_root = skills_dir / "_downloads"
    temp_root.mkdir(parents=True, exist_ok=True)
    temp_path = temp_root / archive_name
    temp_path.write_bytes(archive_bytes)

    try:
        return install_skill_archive_bytes(
            archive_name,
            temp_path.read_bytes(),
            skills_dir=skills_dir,
        )
    finally:
        with contextlib.suppress(OSError):
            temp_path.unlink()
        with contextlib.suppress(OSError):
            if not any(temp_root.iterdir()):
                temp_root.rmdir()


def resolve_clawhub_skill_slug(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return text.strip("/").lower()

    if not _is_clawhub_source(text):
        return ""

    if parsed.path.rstrip("/") == "/api/v1/download":
        query_params = dict(item.split("=", 1) for item in parsed.query.split("&") if "=" in item)
        return str(query_params.get("slug", "")).strip().lower()

    segments = [segment for segment in parsed.path.strip("/").split("/") if segment]
    if not segments:
        return ""
    if len(segments) >= 3 and segments[0] == "api" and segments[1] == "v1" and segments[2] == "skills":
        return str(segments[3] if len(segments) >= 4 else "").strip().lower()
    reserved = {"skills", "upload", "import", "search", "u", "souls", "api"}
    if len(segments) >= 2 and segments[0].lower() not in reserved:
        return segments[-1].strip().lower()
    return ""


async def get_clawhub_skill_details(source_url: str, *, slug: str) -> dict[str, object]:
    skill_slug = resolve_clawhub_skill_slug(slug)
    if not skill_slug:
        raise ValueError("A valid ClawHub skill slug is required.")

    try:
        payload = await _fetch_json(_clawhub_api_url(source_url, f"skills/{skill_slug}"))
    except Exception as exc:
        if _is_rate_limited_error(exc):
            return await _get_clawhub_skill_details_via_html(source_url, skill_slug)
        raise
    if not isinstance(payload, dict):
        raise ValueError("Unexpected ClawHub API response.")

    skill = payload.get("skill", {}) if isinstance(payload.get("skill"), dict) else {}
    owner = payload.get("owner", {}) if isinstance(payload.get("owner"), dict) else {}
    latest_version = payload.get("latestVersion", {}) if isinstance(payload.get("latestVersion"), dict) else {}
    metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
    moderation = payload.get("moderation", {}) if isinstance(payload.get("moderation"), dict) else {}
    security = payload.get("security", {}) if isinstance(payload.get("security"), dict) else {}

    markdown = ""
    for path in ("SKILL.md", "skill.md"):
        try:
            markdown = await _fetch_text(_clawhub_api_url(source_url, f"skills/{skill_slug}/file", path=path))
            if markdown.strip():
                break
        except Exception:
            continue

    owner_handle = str(owner.get("handle", "")).strip()
    web_url = ""
    if owner_handle:
        web_url = urljoin(_site_root(source_url).rstrip("/") + "/", f"{owner_handle}/{skill_slug}")

    return {
        "provider": "clawhub",
        "slug": skill_slug,
        "name": str(skill.get("displayName") or skill.get("slug") or skill_slug),
        "summary": str(skill.get("summary") or ""),
        "owner_handle": owner_handle,
        "owner_name": str(owner.get("displayName") or ""),
        "version": str(latest_version.get("version") or ""),
        "license": str(latest_version.get("license") or ""),
        "updated_at": str(skill.get("updatedAt") or ""),
        "created_at": str(skill.get("createdAt") or ""),
        "tags": skill.get("tags") if isinstance(skill.get("tags"), dict) else {},
        "stats": skill.get("stats") if isinstance(skill.get("stats"), dict) else {},
        "metadata": metadata,
        "moderation": moderation,
        "security": security,
        "skill_markdown": markdown,
        "download_url": _clawhub_api_url(source_url, "download", slug=skill_slug),
        "api_url": _clawhub_api_url(source_url, f"skills/{skill_slug}"),
        "web_url": web_url,
    }


async def search_clawhub_skills(
    source_url: str,
    *,
    query: str = "",
    limit: int = 10,
    non_suspicious_only: bool = True,
    highlighted_only: bool = False,
) -> list[dict[str, object]]:
    bounded_limit = max(1, min(int(limit), 50))
    try:
        if query.strip():
            payload = await _fetch_json(
                _clawhub_api_url(
                    source_url,
                    "search",
                    q=query.strip(),
                    limit=bounded_limit,
                    nonSuspiciousOnly="true" if non_suspicious_only else None,
                    highlightedOnly="true" if highlighted_only else None,
                )
            )
            raw_items = payload.get("results", []) if isinstance(payload, dict) else []
        else:
            payload = await _fetch_json(
                _clawhub_api_url(
                    source_url,
                    "skills",
                    limit=bounded_limit,
                    sort="downloads",
                    nonSuspiciousOnly="true" if non_suspicious_only else None,
                )
            )
            raw_items = payload.get("items", []) if isinstance(payload, dict) else []
    except Exception as exc:
        if not _is_rate_limited_error(exc):
            raise

        search_url = (
            f"{_site_root(source_url).rstrip('/')}/search?q={quote(query.strip())}"
            if query.strip()
            else _site_root(source_url).rstrip("/")
        )
        html_text = await _fetch_text(search_url)
        slugs = _extract_clawhub_slugs_from_html(source_url, html_text)[:bounded_limit]
        return [
            {
                "provider": "clawhub",
                "slug": slug,
                "name": slug.replace("-", " ").title(),
                "summary": "",
                "version": "",
                "updated_at": "",
                "score": None,
                "download_url": "",
                "api_url": "",
            }
            for slug in slugs
        ]

    if not isinstance(raw_items, list):
        return []

    results: list[dict[str, object]] = []
    for item in raw_items[:bounded_limit]:
        if not isinstance(item, dict):
            continue
        slug = resolve_clawhub_skill_slug(str(item.get("slug") or item.get("skill", {}).get("slug", "")))
        if not slug:
            continue

        skill = item.get("skill", {}) if isinstance(item.get("skill"), dict) else item
        latest_version = item.get("latestVersion", {}) if isinstance(item.get("latestVersion"), dict) else {}
        results.append(
            {
                "provider": "clawhub",
                "slug": slug,
                "name": str(skill.get("displayName") or slug),
                "summary": str(skill.get("summary") or ""),
                "version": str(item.get("version") or latest_version.get("version") or ""),
                "updated_at": str(skill.get("updatedAt") or item.get("updatedAt") or ""),
                "score": item.get("score") if isinstance(item.get("score"), (int, float)) else None,
                "download_url": _clawhub_api_url(source_url, "download", slug=slug),
                "api_url": _clawhub_api_url(source_url, f"skills/{slug}"),
            }
        )
    return results


async def install_skill_from_clawhub(
    source_url: str,
    *,
    slug: str,
    version: str = "",
    tag: str = "",
) -> dict[str, object]:
    skill_slug = resolve_clawhub_skill_slug(slug)
    if not skill_slug:
        raise ValueError("A valid ClawHub skill slug is required.")

    details = await get_clawhub_skill_details(source_url, slug=skill_slug)
    archive_url = str(details.get("download_url", "")).strip() or _clawhub_api_url(
        source_url,
        "download",
        slug=skill_slug,
        version=version.strip(),
        tag=tag.strip(),
    )

    try:
        archive_bytes = await _fetch_bytes(archive_url)
    except Exception as exc:
        if not _is_rate_limited_error(exc):
            raise
        fallback_url = await _resolve_clawhub_download_url_via_html(source_url, skill_slug)
        if not fallback_url or fallback_url == archive_url:
            raise
        archive_url = fallback_url
        archive_bytes = await _fetch_bytes(archive_url)

    archive_suffix = version.strip() or tag.strip() or str(details.get("version", "")).strip() or "latest"
    settings = get_settings()
    archive_name = f"{skill_slug}-{_safe_name(archive_suffix)}.zip"
    result = _install_skill_archive_with_cleanup(
        archive_name,
        archive_bytes,
        skills_dir=settings.data_dir / "skills",
    )
    result.update(
        {
            "provider": "clawhub",
            "slug": skill_slug,
            "download_url": archive_url,
            "api_url": str(details.get("api_url", "")),
            "web_url": str(details.get("web_url", "")),
            "version": str(details.get("version", "")),
        }
    )
    return result


def _candidate_skill_manifest_urls(source_url: str) -> list[str]:
    base = source_url.rstrip("/") + "/"
    return [
        urljoin(base, ".well-known/deepcode-skills.json"),
        urljoin(base, "deepcode-skills.json"),
        urljoin(base, "skills.json"),
        urljoin(base, "index.json"),
    ]


def _candidate_mcp_manifest_urls(source_url: str) -> list[str]:
    base = source_url.rstrip("/") + "/"
    return [
        urljoin(base, ".well-known/mcp-servers.json"),
        urljoin(base, "mcp-servers.json"),
        urljoin(base, "servers.json"),
        urljoin(base, "deepcode-mcp.json"),
        urljoin(base, "index.json"),
    ]


def _skill_rows_from_payload(payload: object) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict):
        candidates = payload.get("skills", payload.get("items", payload.get("prompts", [])))
    else:
        candidates = []
    if not isinstance(candidates, list):
        return []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("id") or "").strip()
        if not name:
            continue
        rows.append(
            {
                "name": name,
                "description": str(item.get("description", "")).strip(),
                "content": str(item.get("content", "")),
                "url": str(item.get("url", "")).strip(),
            }
        )
    return rows


def _mcp_rows_from_payload(payload: object) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict):
        candidates = payload.get("servers", payload.get("mcp_servers", payload.get("items", [])))
    else:
        candidates = []
    if not isinstance(candidates, list):
        return []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("id") or item.get("title") or "").strip()
        if not name:
            continue
        command = str(item.get("command") or item.get("url") or item.get("endpoint") or "").strip()
        transport = str(item.get("transport", "")).strip().lower()
        if not transport:
            transport = "http" if command.startswith(("http://", "https://")) else "stdio"
        args = item.get("args", [])
        if not isinstance(args, list):
            args = []
        rows.append(
            {
                "name": name,
                "transport": transport,
                "command": command,
                "args": [str(arg).strip() for arg in args if str(arg).strip()],
                "description": str(item.get("description", "")).strip(),
                "enabled": bool(item.get("enabled", True)),
            }
        )
    return rows


async def _github_markdown_files(owner: str, repo: str, branch: str = "main") -> list[dict[str, str]]:
    api_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    payload = await _fetch_json(api_url)
    if not isinstance(payload, dict):
        return []
    tree = payload.get("tree")
    if not isinstance(tree, list):
        return []
    rows: list[dict[str, str]] = []
    for item in tree:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip()
        if not path.endswith(".md"):
            continue
        lowered = path.lower()
        if not (
            lowered.startswith("skills/")
            or lowered.startswith("prompts/")
            or lowered.startswith("agent/skills/")
            or lowered.startswith("docs/skills/")
        ):
            continue
        rows.append(
            {
                "name": Path(path).stem,
                "description": f"Imported from {owner}/{repo}:{path}",
                "url": f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}",
                "content": "",
            }
        )
    return rows


async def _resolve_remote_skills(source_url: str) -> list[dict[str, str]]:
    if _is_clawhub_source(source_url):
        return []

    for manifest_url in _candidate_skill_manifest_urls(source_url):
        try:
            payload = await _fetch_json(manifest_url)
        except Exception:
            continue
        rows = _skill_rows_from_payload(payload)
        if rows:
            return rows

    repo = _github_repo_from_url(source_url)
    if repo is None:
        return []
    owner, name = repo
    for branch in ("main", "master"):
        try:
            rows = await _github_markdown_files(owner, name, branch=branch)
        except Exception:
            continue
        if rows:
            return rows
    return []


async def _resolve_remote_mcp_servers(source_url: str) -> list[dict[str, object]]:
    for manifest_url in _candidate_mcp_manifest_urls(source_url):
        try:
            payload = await _fetch_json(manifest_url)
        except Exception:
            continue
        rows = _mcp_rows_from_payload(payload)
        if rows:
            return rows
    return []


async def install_skills_from_remote(
    source_url: str,
    *,
    max_items: int = 20,
) -> dict[str, object]:
    if _is_clawhub_source(source_url):
        slug = resolve_clawhub_skill_slug(source_url)
        if not slug:
            return {
                "installed": [],
                "errors": ["ClawHub source requires a specific skill slug or detail URL. Search first, then install by slug."],
            }
        try:
            installed = await install_skill_from_clawhub(source_url, slug=slug)
        except Exception as exc:
            return {"installed": [], "errors": [str(exc)]}
        return {
            "installed": [str(installed.get("install_dir", ""))],
            "errors": [],
            "provider": "clawhub",
            "skill": installed,
        }

    settings = get_settings()
    skills_root = settings.data_dir / "skills" / "remote" / _source_slug(source_url)
    skills_root.mkdir(parents=True, exist_ok=True)

    resolved = await _resolve_remote_skills(source_url)
    if not resolved:
        return {"installed": [], "errors": [f"No installable skills found from {source_url}"]}

    installed: list[str] = []
    errors: list[str] = []
    for item in resolved[: max(max_items, 1)]:
        name = _safe_name(item.get("name", "skill"))
        target = skills_root / f"{name}.md"
        content = str(item.get("content", "")).strip()
        source = str(item.get("url", "")).strip()
        try:
            if not content and source:
                content = await _fetch_text(urljoin(source_url.rstrip("/") + "/", source))
            if not content:
                errors.append(f"{name}: empty content")
                continue
            target.write_text(content, encoding="utf-8")
            installed.append(str(target))
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    return {"installed": installed, "errors": errors}


async def install_mcp_from_remote(
    source_url: str,
    *,
    registry: MCPRegistry | None = None,
    max_items: int = 30,
) -> dict[str, object]:
    resolved = await _resolve_remote_mcp_servers(source_url)
    if not resolved:
        return {"installed": [], "errors": [f"No installable MCP servers found from {source_url}"]}

    target_registry = registry or MCPRegistry()
    installed: list[str] = []
    errors: list[str] = []

    for item in resolved[: max(max_items, 1)]:
        command = str(item.get("command", "")).strip()
        name = str(item.get("name", "")).strip()
        if not name or not command:
            errors.append(f"{name or 'unknown'}: missing required fields")
            continue
        try:
            target_registry.upsert(
                MCPServerConfig(
                    name=name,
                    transport=str(item.get("transport", "stdio")).strip() or "stdio",
                    command=command,
                    args=[str(arg) for arg in item.get("args", []) if str(arg).strip()],
                    description=str(item.get("description", "")).strip(),
                    enabled=bool(item.get("enabled", True)),
                )
            )
            installed.append(name)
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    return {"installed": installed, "errors": errors}

