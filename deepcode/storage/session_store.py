"""Persistent session storage using SQLite via raw aiosqlite."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from deepcode.config import get_settings
from deepcode.exceptions import SessionNotFoundError
from deepcode.logging_config import get_logger

logger = get_logger(__name__)


class Message(BaseModel):
    """A single conversation message."""

    role: str
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Session(BaseModel):
    """A conversation session with message history and metadata."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(default="New Session")
    messages: list[Message] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionStore:
    """Simple JSON-backed session store using SQLite via raw aiosqlite.

    Sessions are serialised as JSON blobs in a single SQLite table.

    Args:
        db_url: Database connection URL (SQLite only).
    """

    def __init__(self, db_url: str | None = None) -> None:
        settings = get_settings()
        raw_url = db_url or settings.resolved_db_url
        # Extract the file path from sqlite+aiosqlite:///path or sqlite:///path
        self._db_path = raw_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
        self._initialised = False

    async def _init(self) -> None:
        """Create the sessions table if it does not exist."""
        if self._initialised:
            return

        from pathlib import Path

        import aiosqlite

        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.commit()

        self._initialised = True

    async def create(self, name: str = "New Session") -> Session:
        """Create and persist a new session.

        Args:
            name: Human-readable session name.

        Returns:
            The newly created :class:`Session`.
        """
        await self._init()
        session = Session(name=name)
        await self._save(session)
        logger.info("Session created", session_id=session.id)
        return session

    async def get(self, session_id: str) -> Session:
        """Retrieve a session by ID.

        Args:
            session_id: UUID of the session.

        Returns:
            The :class:`Session`.

        Raises:
            SessionNotFoundError: If no session with *session_id* exists.
        """
        await self._init()
        import aiosqlite

        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT data FROM sessions WHERE id = ?", (session_id,)
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            raise SessionNotFoundError(f"Session '{session_id}' not found")

        return Session.model_validate_json(row[0])

    async def list_all(self) -> list[Session]:
        """Return all sessions ordered by most recently updated.

        Returns:
            List of :class:`Session` objects.
        """
        await self._init()
        import aiosqlite

        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT data FROM sessions ORDER BY updated_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()

        return [Session.model_validate_json(row[0]) for row in rows]

    async def update(self, session: Session) -> None:
        """Persist changes to an existing session.

        Args:
            session: The updated session to save.
        """
        await self._init()
        session.updated_at = datetime.now(timezone.utc)
        await self._save(session)

    async def delete(self, session_id: str) -> None:
        """Delete a session by ID.

        Args:
            session_id: UUID of the session to delete.

        Raises:
            SessionNotFoundError: If no session with *session_id* exists.
        """
        await self._init()
        import aiosqlite

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "DELETE FROM sessions WHERE id = ?", (session_id,)
            )
            await db.commit()
            if cursor.rowcount == 0:
                raise SessionNotFoundError(f"Session '{session_id}' not found")

        logger.info("Session deleted", session_id=session_id)

    async def _save(self, session: Session) -> None:
        """Insert or replace *session* in the database."""
        import aiosqlite

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO sessions (id, name, data, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.name,
                    session.model_dump_json(),
                    session.created_at.isoformat(),
                    session.updated_at.isoformat(),
                ),
            )
            await db.commit()
