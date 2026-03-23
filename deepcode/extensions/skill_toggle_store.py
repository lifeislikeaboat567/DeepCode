"""Persistent enabled/disabled state for discovered skills."""

from __future__ import annotations

import json
from pathlib import Path

from deepcode.config import get_settings


class SkillToggleStore:
    """File-backed store for skill exposure flags."""

    def __init__(self, file_path: str | None = None) -> None:
        settings = get_settings()
        default_path = settings.data_dir / "skill_toggles.json"
        self._file_path = Path(file_path) if file_path else default_path

    def load(self) -> dict[str, bool]:
        """Load enabled flags keyed by skill path."""
        if not self._file_path.exists():
            return {}

        try:
            payload = json.loads(self._file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

        rows: object
        if isinstance(payload, dict):
            rows = payload.get("skills", payload.get("items", payload))
        else:
            rows = payload

        mapping: dict[str, bool] = {}
        if isinstance(rows, dict):
            for key, value in rows.items():
                mapping[str(key)] = bool(value)
            return mapping

        if isinstance(rows, list):
            for item in rows:
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path", "")).strip()
                if not path:
                    continue
                mapping[path] = bool(item.get("enabled", True))
        return mapping

    def save(self, flags: dict[str, bool]) -> None:
        """Persist enabled flags."""
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "skills": [
                {"path": key, "enabled": bool(value)}
                for key, value in sorted(flags.items(), key=lambda pair: pair[0])
            ]
        }
        self._file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def is_enabled(self, path: str, *, default: bool = True) -> bool:
        """Return whether one skill path is enabled."""
        flags = self.load()
        if path not in flags:
            return default
        return bool(flags[path])

    def set_enabled(self, path: str, enabled: bool) -> None:
        """Set one skill path enablement."""
        normalized = str(path).strip()
        if not normalized:
            return
        flags = self.load()
        flags[normalized] = bool(enabled)
        self.save(flags)
