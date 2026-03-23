"""Approval request persistence for governance ask-flow."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from deepcode.config import get_settings

ApprovalStatus = Literal["pending", "approved", "rejected"]


class ApprovalRequest(BaseModel):
    """One pending or resolved approval request."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str
    action_input: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    rule_id: str = ""
    status: ApprovalStatus = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ApprovalStore:
    """File-backed store for approval requests."""

    def __init__(self, file_path: str | None = None) -> None:
        settings = get_settings()
        self._file_path = Path(file_path) if file_path else (settings.data_dir / "approvals.json")

    def _load_all(self) -> list[ApprovalRequest]:
        if not self._file_path.exists():
            return []
        try:
            payload = json.loads(self._file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        rows = payload.get("requests", []) if isinstance(payload, dict) else payload
        return [ApprovalRequest.model_validate(item) for item in rows]

    def _save_all(self, requests: list[ApprovalRequest]) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"requests": [item.model_dump(mode="json") for item in requests]}
        self._file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def create(
        self,
        tool_name: str,
        action_input: dict[str, Any] | None = None,
        reason: str = "",
        rule_id: str = "",
    ) -> ApprovalRequest:
        request = ApprovalRequest(
            tool_name=tool_name,
            action_input=action_input or {},
            reason=reason,
            rule_id=rule_id,
        )
        rows = self._load_all()
        rows.append(request)
        self._save_all(rows)
        return request

    def list_all(self, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
        rows = self._load_all()
        if status is None:
            return rows
        return [item for item in rows if item.status == status]

    def get(self, request_id: str) -> ApprovalRequest | None:
        for item in self._load_all():
            if item.id == request_id:
                return item
        return None

    def decide(self, request_id: str, decision: Literal["approved", "rejected"]) -> ApprovalRequest | None:
        rows = self._load_all()
        for idx, item in enumerate(rows):
            if item.id != request_id:
                continue
            item.status = decision
            item.updated_at = datetime.now(timezone.utc)
            rows[idx] = item
            self._save_all(rows)
            return item
        return None
