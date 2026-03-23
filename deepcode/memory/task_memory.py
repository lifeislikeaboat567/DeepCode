"""Task memory persistence and retrieval for high-agency workflows."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from deepcode.config import get_settings
from deepcode.memory.long_term import LongTermMemory


class TaskMemoryEntry(BaseModel):
    """One durable task memory entry."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    task_id: str = ""
    source: str = ""
    status: str = "completed"
    user_request: str = ""
    outcome_summary: str = ""
    process_summary: str = ""
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskMemoryStore:
    """File-backed task memory with optional vector retrieval."""

    def __init__(self, file_path: str | None = None, vector_memory: LongTermMemory | None = None) -> None:
        settings = get_settings()
        self._file_path = Path(file_path) if file_path else (settings.data_dir / "task_memory.json")
        collection = f"{settings.vector_collection}_task_memory"
        self._vector_memory = vector_memory or LongTermMemory(collection_name=collection)

    def _load_entries(self) -> list[TaskMemoryEntry]:
        if not self._file_path.exists():
            return []

        try:
            payload = json.loads(self._file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

        rows = payload.get("entries", []) if isinstance(payload, dict) else payload
        entries: list[TaskMemoryEntry] = []
        if not isinstance(rows, list):
            return entries
        for item in rows:
            if not isinstance(item, dict):
                continue
            try:
                entries.append(TaskMemoryEntry.model_validate(item))
            except Exception:
                continue
        return entries

    def _save_entries(self, entries: list[TaskMemoryEntry]) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"entries": [item.model_dump(mode="json") for item in entries]}
        self._file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _build_document(entry: TaskMemoryEntry) -> str:
        rows = [
            f"request: {entry.user_request}",
            f"outcome: {entry.outcome_summary}",
            f"process: {entry.process_summary}",
        ]
        return "\n".join(rows)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        lowered = str(text or "").lower()
        return re.findall(r"[a-z0-9_\-\u4e00-\u9fff]{2,}", lowered)

    def record(
        self,
        *,
        user_request: str,
        outcome_summary: str,
        process_summary: str,
        session_id: str = "",
        task_id: str = "",
        source: str = "",
        status: str = "completed",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskMemoryEntry:
        """Persist one task memory entry and index it for retrieval."""
        entry = TaskMemoryEntry(
            session_id=str(session_id or "").strip(),
            task_id=str(task_id or "").strip(),
            source=str(source or "").strip(),
            status=str(status or "completed").strip() or "completed",
            user_request=str(user_request or "").strip(),
            outcome_summary=str(outcome_summary or "").strip(),
            process_summary=str(process_summary or "").strip(),
            tags=[str(tag).strip() for tag in (tags or []) if str(tag).strip()],
            metadata=metadata or {},
        )
        entries = self._load_entries()
        entries.append(entry)
        self._save_entries(entries[-2000:])

        vector_metadata = {
            "session_id": entry.session_id,
            "task_id": entry.task_id,
            "source": entry.source,
            "status": entry.status,
            "tags": ",".join(entry.tags),
        }
        self._vector_memory.add(entry.id, self._build_document(entry), vector_metadata)
        return entry

    def list_recent(self, *, session_id: str = "", limit: int = 20) -> list[TaskMemoryEntry]:
        """Return newest memory entries, optionally filtered by session."""
        rows = self._load_entries()
        filtered = rows
        session = str(session_id or "").strip()
        if session:
            filtered = [item for item in rows if item.session_id == session]
        filtered.sort(key=lambda item: item.created_at, reverse=True)
        return filtered[: max(limit, 1)]

    def search(self, query: str, *, session_id: str = "", limit: int = 5) -> list[dict[str, Any]]:
        """Search memory entries via lexical + vector fusion ranking."""
        query_text = str(query or "").strip()
        if not query_text:
            return []

        entries = self._load_entries()
        session = str(session_id or "").strip()
        if session:
            entries = [item for item in entries if item.session_id == session]
        if not entries:
            return []

        terms = self._tokenize(query_text)
        lexical_scores: dict[str, float] = {}
        for item in entries:
            corpus = self._build_document(item).lower()
            if not terms:
                score = 0.0
            else:
                score = float(sum(1 for term in terms if term in corpus))
            if score > 0:
                lexical_scores[item.id] = score

        vector_scores: dict[str, float] = {}
        vector_hits = self._vector_memory.query(query_text, n_results=max(limit * 2, 6))
        for hit in vector_hits:
            hit_id = str(hit.get("id", "")).strip()
            if not hit_id:
                continue
            distance = float(hit.get("distance", 1.0) or 1.0)
            score = max(0.0, 1.0 - distance)
            if score <= 0:
                continue
            vector_scores[hit_id] = max(vector_scores.get(hit_id, 0.0), score)

        scored_rows: list[tuple[float, TaskMemoryEntry]] = []
        for item in entries:
            score = lexical_scores.get(item.id, 0.0) + (vector_scores.get(item.id, 0.0) * 3.0)
            if score <= 0:
                continue
            scored_rows.append((score, item))

        if not scored_rows:
            return []

        scored_rows.sort(key=lambda pair: (pair[0], pair[1].created_at), reverse=True)
        hits: list[dict[str, Any]] = []
        for score, item in scored_rows[: max(limit, 1)]:
            hits.append(
                {
                    "id": item.id,
                    "session_id": item.session_id,
                    "task_id": item.task_id,
                    "source": item.source,
                    "status": item.status,
                    "user_request": item.user_request,
                    "outcome_summary": item.outcome_summary,
                    "process_summary": item.process_summary,
                    "tags": list(item.tags),
                    "metadata": dict(item.metadata),
                    "created_at": item.created_at.isoformat(),
                    "score": round(float(score), 4),
                }
            )
        return hits

    def delete_session_entries(self, session_id: str) -> int:
        """Delete all memory entries associated with one session."""
        target = str(session_id or "").strip()
        if not target:
            return 0

        rows = self._load_entries()
        kept: list[TaskMemoryEntry] = []
        removed: list[TaskMemoryEntry] = []
        for item in rows:
            if item.session_id == target:
                removed.append(item)
            else:
                kept.append(item)

        if not removed:
            return 0

        self._save_entries(kept)
        for item in removed:
            try:
                self._vector_memory.delete(item.id)
            except Exception:
                continue
        return len(removed)
