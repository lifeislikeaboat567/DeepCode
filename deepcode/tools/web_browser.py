"""Simple webpage browsing tool for agent use."""

from __future__ import annotations

import html
import json
import re
from urllib.parse import urljoin, urlparse

import httpx

from deepcode.tools.base import BaseTool, ToolResult


def _looks_like_http_url(value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _extract_title(raw_html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return html.unescape(match.group(1)).strip()


def _extract_links(raw_html: str, base_url: str, limit: int = 20) -> list[str]:
    links: list[str] = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', raw_html, re.IGNORECASE):
        value = str(href).strip()
        if not value or value.startswith(("#", "mailto:", "javascript:")):
            continue
        resolved = urljoin(base_url, value)
        if resolved in links:
            continue
        links.append(resolved)
        if len(links) >= limit:
            break
    return links


def _html_to_text(raw_html: str, max_chars: int) -> str:
    cleaned = re.sub(r"<script.*?>.*?</script>", " ", raw_html, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<style.*?>.*?</style>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_chars:
        return cleaned[:max_chars] + " ...(truncated)"
    return cleaned


class WebBrowserTool(BaseTool):
    """Fetch and summarize webpage content for the agent."""

    @property
    def name(self) -> str:
        return "web_browser"

    @property
    def description(self) -> str:
        return "Browse a webpage and extract readable text and links. Actions: fetch."

    async def run(self, action: str, **kwargs: object) -> ToolResult:
        if action != "fetch":
            return ToolResult.fail(self.name, "Unknown action. Use: fetch.")
        try:
            return await self._fetch(**kwargs)
        except Exception as exc:
            return ToolResult.fail(self.name, f"Web browse failed: {exc}")

    async def _fetch(self, **kwargs: object) -> ToolResult:
        url = str(kwargs.get("url", "")).strip()
        if not _looks_like_http_url(url):
            return ToolResult.fail(self.name, "Parameter 'url' must be a valid http(s) URL.")

        timeout_raw = kwargs.get("timeout", 20)
        max_chars_raw = kwargs.get("max_chars", 6000)
        try:
            timeout = max(1.0, float(timeout_raw))
        except (TypeError, ValueError):
            timeout = 20.0
        try:
            max_chars = max(500, int(max_chars_raw))
        except (TypeError, ValueError):
            max_chars = 6000

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            return ToolResult.fail(self.name, f"HTTP request failed: {exc}", url=url)

        body = response.text
        content_type = response.headers.get("content-type", "")
        title = _extract_title(body)
        links = _extract_links(body, str(response.url))
        text = _html_to_text(body, max_chars=max_chars)
        payload = {
            "url": str(response.url),
            "status_code": response.status_code,
            "content_type": content_type,
            "title": title,
            "text": text,
            "links": links,
        }

        if response.status_code >= 400:
            return ToolResult.fail(self.name, json.dumps(payload, ensure_ascii=False, indent=2), url=str(response.url))

        return ToolResult.ok(self.name, json.dumps(payload, ensure_ascii=False, indent=2), url=str(response.url), title=title)
