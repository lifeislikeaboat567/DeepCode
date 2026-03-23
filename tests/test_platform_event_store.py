"""Tests for persistent platform event id idempotency store."""

from __future__ import annotations

import pytest

from deepcode.storage import PlatformEventIdStore


@pytest.mark.asyncio
async def test_platform_event_store_deduplicates_same_event_id(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'platform-event-store.db'}"
    store = PlatformEventIdStore(db_url=db_url)

    first = await store.is_duplicate_or_store("feishu", "evt-1", ttl_seconds=3600)
    second = await store.is_duplicate_or_store("feishu", "evt-1", ttl_seconds=3600)

    assert first is False
    assert second is True


@pytest.mark.asyncio
async def test_platform_event_store_namespaces_by_platform(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'platform-event-store.db'}"
    store = PlatformEventIdStore(db_url=db_url)

    first = await store.is_duplicate_or_store("feishu", "evt-shared", ttl_seconds=3600)
    second = await store.is_duplicate_or_store("wechat", "evt-shared", ttl_seconds=3600)

    assert first is False
    assert second is False
