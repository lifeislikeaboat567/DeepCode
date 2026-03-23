"""Skill discovery and metadata index for markdown skill files."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from deepcode.config import get_settings


class SkillDefinition(BaseModel):
    """A discovered skill document."""

    name: str
    path: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class SkillRegistry:
    """Discover skills from filesystem directories."""

    def __init__(self, skills_dir: str | None = None) -> None:
        settings = get_settings()
        self._skills_dir = Path(skills_dir) if skills_dir else (settings.data_dir / "skills")

    def discover(self) -> list[SkillDefinition]:
        """Discover markdown-based skill files."""
        if not self._skills_dir.exists():
            return []

        results: list[SkillDefinition] = []
        package_roots: set[Path] = set()
        marker_files = sorted(
            [file for file in self._skills_dir.rglob("*.md") if file.name.lower() == "skill.md"]
        )

        for file in marker_files:
            package_root = file.parent
            package_roots.add(package_root)
            skill_name = package_root.name if package_root != self._skills_dir else file.stem
            results.append(
                SkillDefinition(
                    name=skill_name,
                    path=str(file),
                    description=self._extract_first_heading(file),
                    tags=self._extract_tags(file),
                )
            )

        for file in sorted(self._skills_dir.rglob("*.md")):
            if file.name.lower() == "skill.md":
                continue
            if any(root in file.parents for root in package_roots):
                continue
            results.append(
                SkillDefinition(
                    name=file.stem,
                    path=str(file),
                    description=self._extract_first_heading(file),
                    tags=self._extract_tags(file),
                )
            )
        return results

    def _extract_first_heading(self, path: Path) -> str:
        """Extract first markdown heading as short description."""
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("#"):
                    return line.lstrip("#").strip()
        except OSError:
            return ""
        return ""

    def _extract_tags(self, path: Path) -> list[str]:
        """Extract #tag tokens from skill document."""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []

        tags: set[str] = set()
        for token in text.replace("\n", " ").split(" "):
            token = token.strip()
            if token.startswith("#") and len(token) > 1 and token[1:].isalnum():
                tags.add(token[1:].lower())
        return sorted(tags)
