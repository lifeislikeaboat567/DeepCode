"""Unit tests for the short-term and long-term memory modules."""

from __future__ import annotations

from deepcode.memory.task_memory import TaskMemoryStore
from deepcode.llm.base import LLMMessage
from deepcode.memory.short_term import ShortTermMemory


class TestShortTermMemory:
    def test_initial_state_is_empty(self):
        mem = ShortTermMemory(max_messages=10)
        assert len(mem) == 0

    def test_add_and_get_messages(self):
        mem = ShortTermMemory()
        mem.add("user", "Hello")
        mem.add("assistant", "Hi")
        messages = mem.get_messages(include_system=False)
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[1].role == "assistant"

    def test_system_prompt_is_prepended(self):
        mem = ShortTermMemory(system_prompt="You are helpful")
        mem.add("user", "Hello")
        messages = mem.get_messages(include_system=True)
        assert messages[0].role == "system"
        assert messages[0].content == "You are helpful"
        assert len(messages) == 2  # system + user

    def test_system_prompt_excluded_when_requested(self):
        mem = ShortTermMemory(system_prompt="prompt")
        mem.add("user", "Hi")
        messages = mem.get_messages(include_system=False)
        assert all(m.role != "system" for m in messages)

    def test_trimming_oldest_messages(self):
        mem = ShortTermMemory(max_messages=3)
        for i in range(6):
            mem.add("user", f"message {i}")
        assert len(mem) == 3
        messages = mem.get_messages(include_system=False)
        # Should retain the most recent 3
        assert messages[0].content == "message 3"
        assert messages[-1].content == "message 5"

    def test_clear_empties_history(self):
        mem = ShortTermMemory()
        mem.add("user", "hi")
        mem.clear()
        assert len(mem) == 0

    def test_add_llm_message(self):
        mem = ShortTermMemory()
        msg = LLMMessage(role="user", content="direct message")
        mem.add_message(msg)
        messages = mem.get_messages(include_system=False)
        assert len(messages) == 1
        assert messages[0].content == "direct message"

    def test_get_messages_returns_copy(self):
        mem = ShortTermMemory()
        mem.add("user", "hello")
        msgs1 = mem.get_messages(include_system=False)
        msgs2 = mem.get_messages(include_system=False)
        assert msgs1 is not msgs2


class _FakeVectorMemory:
    def __init__(self) -> None:
        self.documents: dict[str, str] = {}

    def add(self, entry_id: str, text: str, metadata=None) -> None:
        self.documents[entry_id] = text

    def query(self, query_text: str, n_results: int = 5):
        rows = []
        for key, value in self.documents.items():
            if query_text.lower() in value.lower():
                rows.append(
                    {
                        "id": key,
                        "document": value,
                        "metadata": {},
                        "distance": 0.05,
                    }
                )
        return rows[:n_results]

    def delete(self, entry_id: str) -> None:
        self.documents.pop(entry_id, None)


class TestTaskMemoryStore:
    def test_record_and_search(self, tmp_path):
        store = TaskMemoryStore(
            file_path=str(tmp_path / "task_memory.json"),
            vector_memory=_FakeVectorMemory(),
        )

        store.record(
            session_id="session-a",
            task_id="task-1",
            source="chat_agent",
            user_request="build parser",
            outcome_summary="parser implemented",
            process_summary="wrote script and executed tests",
        )

        hits = store.search("parser", limit=3)
        assert len(hits) == 1
        assert hits[0]["task_id"] == "task-1"
        assert "implemented" in hits[0]["outcome_summary"]

    def test_delete_session_entries(self, tmp_path):
        store = TaskMemoryStore(
            file_path=str(tmp_path / "task_memory.json"),
            vector_memory=_FakeVectorMemory(),
        )

        store.record(
            session_id="session-a",
            task_id="task-1",
            source="orchestrator",
            user_request="task a",
            outcome_summary="done a",
            process_summary="steps a",
        )
        store.record(
            session_id="session-b",
            task_id="task-2",
            source="orchestrator",
            user_request="task b",
            outcome_summary="done b",
            process_summary="steps b",
        )

        removed = store.delete_session_entries("session-a")
        assert removed == 1

        remaining = store.list_recent(limit=10)
        assert len(remaining) == 1
        assert remaining[0].session_id == "session-b"
