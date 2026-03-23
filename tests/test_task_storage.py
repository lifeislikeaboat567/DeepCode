"""Unit tests for task storage."""

from __future__ import annotations

import pytest

from deepcode.exceptions import TaskNotFoundError
from deepcode.storage import TaskStore


@pytest.fixture
def store(tmp_session_db: str) -> TaskStore:
    return TaskStore(db_url=tmp_session_db)


class TestTaskStore:
    @pytest.mark.asyncio
    async def test_create_and_get_task(self, store: TaskStore):
        created = await store.create(task="Build feature")
        fetched = await store.get(created.id)
        assert fetched.id == created.id
        assert fetched.task == "Build feature"
        assert fetched.status == "pending"

    @pytest.mark.asyncio
    async def test_set_status_updates_fields(self, store: TaskStore):
        created = await store.create(task="Build endpoint")
        updated = await store.set_status(
            created.id,
            "completed",
            summary="Done",
            plan=["step one"],
            code_artifacts=[{"filename": "main.py", "content": "print('ok')"}],
            execution_results=[{"step_id": "step-1", "success": True}],
            task_state={"status": "completed", "goal": "Build endpoint"},
            observations=[{"source": "planner", "summary": "planned"}],
            reflections=[{"diagnosis": "none"}],
            errors=[],
        )
        assert updated.status == "completed"
        assert updated.summary == "Done"
        assert updated.plan == ["step one"]
        assert len(updated.code_artifacts) == 1
        assert updated.execution_results[0]["step_id"] == "step-1"
        assert updated.task_state["status"] == "completed"
        assert updated.observations[0]["source"] == "planner"

    @pytest.mark.asyncio
    async def test_list_all_returns_recent_tasks(self, store: TaskStore):
        a = await store.create(task="A")
        b = await store.create(task="B")
        tasks = await store.list_all(limit=10)
        ids = [task.id for task in tasks]
        assert a.id in ids
        assert b.id in ids

    @pytest.mark.asyncio
    async def test_delete_removes_task(self, store: TaskStore):
        created = await store.create(task="Delete me")
        await store.delete(created.id)
        with pytest.raises(TaskNotFoundError):
            await store.get(created.id)

    @pytest.mark.asyncio
    async def test_get_unknown_task_raises(self, store: TaskStore):
        with pytest.raises(TaskNotFoundError):
            await store.get("missing-task-id")
