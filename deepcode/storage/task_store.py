"""Persistent task storage backed by SQLite."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from deepcode.config import get_settings
from deepcode.exceptions import TaskNotFoundError
from deepcode.logging_config import get_logger

logger = get_logger(__name__)


class TaskRecord(BaseModel):
    """Stored task execution record."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task: str
    session_id: str | None = None
    status: str = "pending"
    plan: list[str] = Field(default_factory=list)
    code_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    review_result: dict[str, Any] = Field(default_factory=dict)
    execution_results: list[dict[str, Any]] = Field(default_factory=list)
    task_state: dict[str, Any] = Field(default_factory=dict)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    reflections: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    error: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskStore:
    """Simple JSON-backed task store over SQLite."""

    def __init__(self, db_url: str | None = None) -> None:
        settings = get_settings()
        raw_url = db_url or settings.resolved_db_url
        self._db_path = raw_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
        self._initialised = False

    async def _init(self) -> None:
        if self._initialised:
            return

        from pathlib import Path

        import aiosqlite

        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.commit()

        self._initialised = True

    async def create(
        self,
        task: str,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskRecord:
        """Create and persist a new task record."""
        await self._init()
        record = TaskRecord(task=task, session_id=session_id, metadata=metadata or {})
        await self._save(record)
        logger.info("Task created", task_id=record.id)
        return record

    async def get(self, task_id: str) -> TaskRecord:
        """Retrieve task by id."""
        await self._init()
        import aiosqlite

        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute("SELECT data FROM tasks WHERE id = ?", (task_id,)) as cursor:
                row = await cursor.fetchone()

        if row is None:
            raise TaskNotFoundError(f"Task '{task_id}' not found")

        return TaskRecord.model_validate_json(row[0])

    async def list_all(self, limit: int = 50) -> list[TaskRecord]:
        """List tasks ordered by most recent updates."""
        await self._init()
        import aiosqlite

        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT data FROM tasks ORDER BY updated_at DESC LIMIT ?",
                (max(limit, 1),),
            ) as cursor:
                rows = await cursor.fetchall()

        return [TaskRecord.model_validate_json(row[0]) for row in rows]

    async def update(self, record: TaskRecord) -> None:
        """Persist updates to an existing task."""
        await self._init()
        record.updated_at = datetime.now(timezone.utc)
        await self._save(record)

    async def set_status(self, task_id: str, status: str, **updates: Any) -> TaskRecord:
        """Set task status and optional fields, then persist and return it."""
        record = await self.get(task_id)
        record.status = status
        for key, value in updates.items():
            if hasattr(record, key):
                setattr(record, key, value)
        await self.update(record)
        return record

    async def delete(self, task_id: str) -> None:
        """Delete a task by id."""
        await self._init()
        import aiosqlite

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            await db.commit()
            if cursor.rowcount == 0:
                raise TaskNotFoundError(f"Task '{task_id}' not found")

        logger.info("Task deleted", task_id=task_id)

    async def _save(self, record: TaskRecord) -> None:
        """Insert or replace a task record."""
        import aiosqlite

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO tasks (id, status, data, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.status,
                    record.model_dump_json(),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            await db.commit()
