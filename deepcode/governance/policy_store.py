"""Policy rule persistence for governance decisions."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from deepcode.config import get_settings

PolicyDecision = Literal["allow", "ask", "deny"]


class PolicyRule(BaseModel):
    """One policy decision rule."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    scope: str = "global"
    target: str = "*"
    decision: PolicyDecision = "ask"
    enabled: bool = True
    description: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PolicyStore:
    """File-backed policy rule store."""

    def __init__(self, file_path: str | None = None) -> None:
        settings = get_settings()
        self._file_path = Path(file_path) if file_path else (settings.data_dir / "policies.json")

    def load(self) -> list[PolicyRule]:
        """Load policy rules from disk."""
        if not self._file_path.exists():
            return []

        try:
            data = json.loads(self._file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

        rows = data.get("rules", []) if isinstance(data, dict) else data
        return [PolicyRule.model_validate(row) for row in rows]

    def save(self, rules: list[PolicyRule]) -> None:
        """Persist policy rules to disk."""
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"rules": [rule.model_dump(mode="json") for rule in rules]}
        self._file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list_all(self) -> list[PolicyRule]:
        """Return all policy rules."""
        return self.load()

    def upsert(self, rule: PolicyRule) -> PolicyRule:
        """Insert or update one policy rule by id."""
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
        """Remove one policy rule by id."""
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
                    "scope": rule.scope,
                    "target": rule.target,
                    "decision": rule.decision,
                    "enabled": "yes" if rule.enabled else "no",
                    "updated_at": rule.updated_at.isoformat(),
                }
            )
        return rows
