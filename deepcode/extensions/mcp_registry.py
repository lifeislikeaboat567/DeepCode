"""Model Context Protocol server registry and loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from deepcode.config import get_settings


class MCPServerConfig(BaseModel):
    """Configuration for an external MCP server."""

    name: str
    transport: str = Field(default="stdio")
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    description: str = ""


class MCPRegistry:
    """Store and discover MCP server configurations."""

    def __init__(self, config_path: str | None = None) -> None:
        settings = get_settings()
        default_path = settings.data_dir / "mcp_servers.json"
        self._config_path = Path(config_path) if config_path else default_path

    def load(self) -> list[MCPServerConfig]:
        """Load configured MCP servers from disk."""
        if not self._config_path.exists():
            return []

        data = json.loads(self._config_path.read_text(encoding="utf-8"))
        servers = data.get("servers", []) if isinstance(data, dict) else data
        return [MCPServerConfig.model_validate(item) for item in servers]

    def save(self, servers: list[MCPServerConfig]) -> None:
        """Persist MCP server configurations."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"servers": [s.model_dump() for s in servers]}
        self._config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def upsert(self, server: MCPServerConfig) -> None:
        """Insert or update one MCP server config by name."""
        servers = self.load()
        updated = False
        for idx, existing in enumerate(servers):
            if existing.name == server.name:
                servers[idx] = server
                updated = True
                break
        if not updated:
            servers.append(server)
        self.save(servers)

    def remove(self, name: str) -> bool:
        """Remove one MCP server config by name."""
        servers = self.load()
        remaining = [s for s in servers if s.name != name]
        if len(remaining) == len(servers):
            return False
        self.save(remaining)
        return True

    def to_rows(self) -> list[dict[str, Any]]:
        """Return list-friendly dictionaries for CLI/UI rendering."""
        return [
            {
                "name": server.name,
                "transport": server.transport,
                "command": server.command,
                "enabled": server.enabled,
                "description": server.description,
            }
            for server in self.load()
        ]
