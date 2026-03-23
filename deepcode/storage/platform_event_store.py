"""Persistent idempotency store for platform event IDs."""

from __future__ import annotations

import time

from deepcode.config import get_settings


class PlatformEventIdStore:
    """Persist and deduplicate platform event IDs in SQLite."""

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
                CREATE TABLE IF NOT EXISTS platform_event_ids (
                    platform TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    PRIMARY KEY (platform, event_id)
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_platform_event_ids_expires_at
                ON platform_event_ids (expires_at)
                """
            )
            await db.commit()

        self._initialised = True

    async def is_duplicate_or_store(
        self,
        platform: str,
        event_id: str,
        *,
        ttl_seconds: int,
    ) -> bool:
        """Return True when event already exists; otherwise store it and return False."""
        normalized_platform = str(platform or "").strip().lower() or "generic"
        normalized_event_id = str(event_id or "").strip()
        if not normalized_event_id:
            return False

        await self._init()
        import aiosqlite

        now = int(time.time())
        expires_at = now + max(int(ttl_seconds), 1)

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "DELETE FROM platform_event_ids WHERE expires_at <= ?",
                (now,),
            )
            before_changes = db.total_changes
            await db.execute(
                """
                INSERT OR IGNORE INTO platform_event_ids (platform, event_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (normalized_platform, normalized_event_id, now, expires_at),
            )
            await db.commit()
            inserted = db.total_changes > before_changes

        return not inserted
