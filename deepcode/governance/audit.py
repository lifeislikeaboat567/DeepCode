"""JSONL-based governance and audit logging."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from deepcode.config import get_settings


class AuditEvent(BaseModel):
    """One immutable audit event."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event: str
    actor: str = "system"
    status: str = "ok"
    resource: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditLogger:
    """Append and query JSONL audit events."""

    def __init__(self, file_path: str | None = None) -> None:
        settings = get_settings()
        self._file_path = Path(file_path) if file_path else (settings.data_dir / "audit.log")

    def write(
        self,
        event: str,
        actor: str = "system",
        status: str = "ok",
        resource: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append one event to audit log file."""
        payload = AuditEvent(
            event=event,
            actor=actor,
            status=status,
            resource=resource,
            metadata=metadata or {},
        )
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        with self._file_path.open("a", encoding="utf-8") as fh:
            fh.write(payload.model_dump_json())
            fh.write("\n")

    def list_recent(self, limit: int = 50) -> list[AuditEvent]:
        """Read most recent N events from audit log."""
        if not self._file_path.exists():
            return []

        lines = self._file_path.read_text(encoding="utf-8").splitlines()
        selected = lines[-max(limit, 1):]
        events: list[AuditEvent] = []
        for line in selected:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                events.append(AuditEvent.model_validate(data))
            except (json.JSONDecodeError, ValueError):
                continue
        return events
