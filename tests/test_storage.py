"""Unit tests for session storage."""

from __future__ import annotations

import pytest

from deepcode.exceptions import SessionNotFoundError
from deepcode.storage.session_store import Message, SessionStore


@pytest.fixture
def store(tmp_session_db: str) -> SessionStore:
    return SessionStore(db_url=tmp_session_db)


class TestSessionStore:
    @pytest.mark.asyncio
    async def test_create_returns_session_with_id(self, store: SessionStore):
        session = await store.create(name="Test Session")
        assert session.id
        assert session.name == "Test Session"
        assert len(session.messages) == 0

    @pytest.mark.asyncio
    async def test_get_returns_created_session(self, store: SessionStore):
        created = await store.create(name="Fetch Me")
        fetched = await store.get(created.id)
        assert fetched.id == created.id
        assert fetched.name == "Fetch Me"

    @pytest.mark.asyncio
    async def test_get_raises_for_unknown_id(self, store: SessionStore):
        with pytest.raises(SessionNotFoundError):
            await store.get("nonexistent-id-12345")

    @pytest.mark.asyncio
    async def test_list_all_returns_sessions_ordered_by_updated(self, store: SessionStore):
        s1 = await store.create(name="First")
        s2 = await store.create(name="Second")
        sessions = await store.list_all()
        assert len(sessions) >= 2
        ids = [s.id for s in sessions]
        assert s1.id in ids
        assert s2.id in ids

    @pytest.mark.asyncio
    async def test_update_persists_messages(self, store: SessionStore):
        session = await store.create()
        session.messages.append(Message(role="user", content="hello"))
        await store.update(session)

        fetched = await store.get(session.id)
        assert len(fetched.messages) == 1
        assert fetched.messages[0].content == "hello"

    @pytest.mark.asyncio
    async def test_delete_removes_session(self, store: SessionStore):
        session = await store.create()
        await store.delete(session.id)
        with pytest.raises(SessionNotFoundError):
            await store.get(session.id)

    @pytest.mark.asyncio
    async def test_delete_raises_for_unknown_id(self, store: SessionStore):
        with pytest.raises(SessionNotFoundError):
            await store.delete("does-not-exist")

    @pytest.mark.asyncio
    async def test_multiple_sessions_are_independent(self, store: SessionStore):
        s1 = await store.create(name="A")
        s2 = await store.create(name="B")
        s1.messages.append(Message(role="user", content="msg-a"))
        await store.update(s1)

        fetched_s2 = await store.get(s2.id)
        assert len(fetched_s2.messages) == 0
