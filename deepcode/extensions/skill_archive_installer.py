"""Install packaged Skills from local zip archives."""

from __future__ import annotations

import io
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath

from deepcode.config import get_settings


def _default_skills_packages_dir() -> Path:
    settings = get_settings()
    settings.ensure_data_dir()
    return settings.data_dir / "skills" / "packages"


def _safe_package_name(stem: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", str(stem).strip())
    normalized = normalized.strip(".-_")
    return normalized or "skill-package"


def _zip_manifest_candidates(names: list[str]) -> list[PurePosixPath]:
    candidates: list[PurePosixPath] = []
    for raw_name in names:
        path = PurePosixPath(raw_name)
        if path.name.lower() == "skill.md":
            candidates.append(path)
    candidates.sort(key=lambda item: (len(item.parts), len(str(item))))
    return candidates


def install_skill_archive_bytes(filename: str, data: bytes, *, skills_dir: str | Path | None = None) -> dict[str, str]:
    """Validate and install one skill archive into the local skills directory."""
    archive_name = str(filename or "").strip()
    if not archive_name.lower().endswith(".zip"):
        raise ValueError("文件格式不对：仅支持 .zip Skills 压缩包")

    stream = io.BytesIO(data)
    if not zipfile.is_zipfile(stream):
        raise ValueError("文件格式不对：压缩包已损坏或不是 zip 文件")

    stream.seek(0)
    with zipfile.ZipFile(stream) as archive:
        members = [info for info in archive.infolist() if not info.is_dir()]
        names = [info.filename for info in members]
        manifest_candidates = _zip_manifest_candidates(names)
        if not manifest_candidates:
            raise ValueError("文件格式不对：压缩包内缺少 SKILL.md")

        manifest_path = manifest_candidates[0]
        manifest_parent = manifest_path.parent
        package_name = _safe_package_name(Path(archive_name).stem)
        root_dir = Path(skills_dir) if skills_dir else _default_skills_packages_dir()
        target_dir = root_dir / package_name

        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        extracted_count = 0
        for info in members:
            member_path = PurePosixPath(info.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError("文件格式不对：压缩包包含非法路径")

            if manifest_parent != PurePosixPath("."):
                try:
                    relative = member_path.relative_to(manifest_parent)
                except ValueError:
                    continue
            else:
                relative = member_path

            if not relative.parts:
                continue

            outfile = target_dir.joinpath(*relative.parts)
            outfile.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, outfile.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            extracted_count += 1

        installed_manifest = target_dir / manifest_path.name
        if not installed_manifest.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
            raise ValueError("文件格式不对：无法在安装目录找到 SKILL.md")

    return {
        "package_name": package_name,
        "install_dir": str(target_dir),
        "manifest_path": str(installed_manifest),
        "files": str(extracted_count),
    }
