"""Unit tests for the short-term and long-term memory modules."""

from __future__ import annotations

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
