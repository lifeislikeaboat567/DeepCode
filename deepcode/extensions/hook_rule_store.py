"""Persistent editable hook rules for UI/CLI governance."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from deepcode.config import get_settings
from deepcode.extensions.hooks import HookEvent

HookHandlerType = Literal["command", "http", "prompt", "agent"]


class HookRule(BaseModel):
    """One editable hook rule."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    event: HookEvent
    handler_type: HookHandlerType = "command"
    handler_value: str = ""
    enabled: bool = True
    description: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HookRuleStore:
    """File-backed hook rule store."""

    def __init__(self, file_path: str | None = None) -> None:
        settings = get_settings()
        self._file_path = Path(file_path) if file_path else (settings.data_dir / "hook_rules.json")

    def load(self) -> list[HookRule]:
        """Load hook rules from disk."""
        if not self._file_path.exists():
            return []

        try:
            data = json.loads(self._file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

        rows = data.get("rules", []) if isinstance(data, dict) else data
        return [HookRule.model_validate(row) for row in rows]

    def save(self, rules: list[HookRule]) -> None:
        """Persist hook rules to disk."""
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"rules": [rule.model_dump(mode="json") for rule in rules]}
        self._file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list_all(self) -> list[HookRule]:
        """Return all hook rules."""
        return self.load()

    def upsert(self, rule: HookRule) -> HookRule:
        """Insert or update hook rule by id."""
        rules = self.load()
        rule.updated_at = datetime.now(timezone.utc)
        for idx, existing in enumerate(rules):
            if existing.id == rule.id:
                rules[idx] = rule
                self.save(rules)
                return rule

        rules.append(rule)
        self.save(rules)
        return rule

    def remove(self, rule_id: str) -> bool:
        """Remove one hook rule by id."""
        rules = self.load()
        remaining = [rule for rule in rules if rule.id != rule_id]
        if len(remaining) == len(rules):
            return False
        self.save(remaining)
        return True

    def to_rows(self) -> list[dict[str, str]]:
        """Return list-friendly dictionaries for UI/CLI tables."""
        rows: list[dict[str, str]] = []
        for rule in self.load():
            rows.append(
                {
                    "id": rule.id,
                    "name": rule.name,
                    "event": rule.event.value,
                    "handler_type": rule.handler_type,
                    "enabled": "yes" if rule.enabled else "no",
                    "updated_at": rule.updated_at.isoformat(),
                }
            )
        return rows
