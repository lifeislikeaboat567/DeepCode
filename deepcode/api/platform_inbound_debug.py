"""JSONL-backed debug log store for inbound platform callback requests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from deepcode.config import get_settings


class PlatformInboundDebugEvent(BaseModel):
    """One captured inbound callback request/response pair."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    platform: str
    method: str = "POST"
    url: str = ""
    path: str = ""
    client: str = ""
    query: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    request_body: str = ""
    response_status: int = 200
    response_body: str = ""


class PlatformInboundDebugStore:
    """Append and query inbound platform callback debug records."""

    def __init__(self, file_path: str | None = None) -> None:
        settings = get_settings()
        self._file_path = Path(file_path) if file_path else (settings.data_dir / "platform_inbound_debug.log")

    def write(self, payload: PlatformInboundDebugEvent) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        with self._file_path.open("a", encoding="utf-8") as fh:
            fh.write(payload.model_dump_json())
            fh.write("\n")

    def list_recent(self, limit: int = 50) -> list[PlatformInboundDebugEvent]:
        if not self._file_path.exists():
            return []

        lines = self._file_path.read_text(encoding="utf-8").splitlines()
        selected = lines[-max(limit, 1) :]
        events: list[PlatformInboundDebugEvent] = []
        for line in selected:
            text = line.strip()
            if not text:
                continue
            try:
                events.append(PlatformInboundDebugEvent.model_validate(json.loads(text)))
            except (json.JSONDecodeError, ValueError):
                continue
        return events

    def clear(self) -> None:
        if self._file_path.exists():
            self._file_path.unlink()
