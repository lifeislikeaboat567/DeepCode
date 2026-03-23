"""Tests for chat runtime Agent-mode context assembly and streaming."""

from __future__ import annotations

from collections.abc import AsyncIterator
import json

import pytest

from deepcode.chat_runtime import (
    build_agent_task_prompt,
    complete_chat_response,
    complete_agent_response,
    normalize_chat_mode,
    stream_agent_events,
    _prepare_agent_runtime_context,
)
from deepcode.llm.base import BaseLLMClient, LLMMessage, LLMResponse
from deepcode.llm.mock_client import MockLLMClient
from deepcode.storage import Message
from deepcode.tools.base import BaseTool, ToolResult


class _SkillTool(BaseTool):
    def __init__(self) -> None:
        self.read_calls: list[str] = []

    @property
    def name(self) -> str:
        return "skill_registry"

    @property
    def description(self) -> str:
        return "skill registry"

    async def run(self, action: str, **kwargs):
        if action == "list":
            payload = [
                {
                    "name": "python_debug",
                    "description": "Debug workflows",
                    "path": "/skills/python_debug.md",
                    "tags": ["python", "debug"],
                },
                {
                    "name": "find-skills-0.1.0",
                    "description": "Discover installed skills and routes",
                    "path": "/skills/find-skills-0.1.0.md",
                    "tags": ["skill", "routing"],
                },
            ]
            return ToolResult.ok(self.name, json.dumps(payload, ensure_ascii=False))
        if action == "read":
            name = str(kwargs.get("name", "")).strip()
            self.read_calls.append(name)
            if name == "find-skills-0.1.0":
                return ToolResult.ok(self.name, "# find-skills\nThis skill explains discovery and routing behavior.\n")
            return ToolResult.ok(self.name, "# Python Debug\nUse pdb and logs.\n")
        return ToolResult.fail(self.name, "unknown action")


class _RecordingLLM(BaseLLMClient):
    def __init__(self, content: str = "ok") -> None:
        self.content = content
        self.last_messages: list[LLMMessage] = []

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.last_messages = list(messages)
        return LLMResponse(content=self.content, model="recording", input_tokens=0, output_tokens=0)

    async def stream_complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        self.last_messages = list(messages)
        yield self.content


class _McpTool(BaseTool):
    @property
    def name(self) -> str:
        return "mcp_service"

    @property
    def description(self) -> str:
        return "mcp service"

    async def run(self, action: str, **kwargs):
        if action == "list_servers":
            payload = [{"name": "docs-index", "transport": "http", "description": "API and docs search"}]
            return ToolResult.ok(self.name, json.dumps(payload, ensure_ascii=False))
        return ToolResult.fail(self.name, "unknown action")


class _EchoTool(BaseTool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "echo"

    async def run(self, text: str = "", **kwargs):
        return ToolResult.ok(self.name, text or "ok")


class _MemoryStoreStub:
    def search(self, query: str, *, limit: int = 5):
        return [
            {
                "id": "mem-1",
                "task_id": "task-1",
                "source": "orchestrator",
                "status": "completed",
                "user_request": "build parser",
                "outcome_summary": "parser and tests completed",
                "process_summary": "created script and validated output",
                "score": 3.2,
            }
        ][:limit]


def _final_answer_response(answer: str) -> str:
    return json.dumps(
        {"thought": "I have the answer", "action": "final_answer", "action_input": {"answer": answer}}
    )


def test_normalize_chat_mode():
    assert normalize_chat_mode("agent") == "agent"
    assert normalize_chat_mode("AGENT") == "agent"
    assert normalize_chat_mode("ask") == "ask"
    assert normalize_chat_mode("other") == "ask"


def test_build_agent_task_prompt_contains_injected_sections():
    history = [Message(role="user", content="Build a script to parse logs and validate output")]
    prompt = build_agent_task_prompt(
        history,
        capability_context="Local tools: script_runner, file_manager",
        relevant_skills=[{"name": "python_debug", "description": "Debug workflows", "path": "/skills/python_debug.md"}],
        relevant_mcp_servers=[{"name": "docs-index", "transport": "http", "description": "API and docs search"}],
        decomposed_task={
            "goal": "Parse logs",
            "constraints": [],
            "success_criteria": ["Parser runs successfully"],
            "deliverables": ["Parser script"],
            "subtasks": ["Read logs", "Parse entries", "Validate schema"],
        },
    )

    assert "Task decomposition" in prompt
    assert "Relevant skills to expose/use" in prompt
    assert "python_debug" in prompt
    assert "docs-index" in prompt


@pytest.mark.asyncio
async def test_prepare_agent_runtime_context_collects_relevant_capabilities():
    llm = MockLLMClient(
        responses=[
            json.dumps(
                {
                    "goal": "Use docs and skills",
                    "constraints": [],
                    "success_criteria": ["Done"],
                    "deliverables": ["Answer"],
                    "subtasks": ["Discover skill", "Inspect mcp", "Execute"],
                }
            )
        ]
    )
    history = [Message(role="user", content="Use docs and skills to implement a parser")]
    tools = [_SkillTool(), _McpTool()]

    runtime = await _prepare_agent_runtime_context(llm, history, tools)

    assert runtime.intent_route.intent in {"context_enrichment", "feature_delivery", "general_engineering"}
    assert len(runtime.relevant_skills) >= 1
    assert len(runtime.relevant_mcp_servers) >= 1
    assert "python_debug" in runtime.task_prompt
    assert "docs-index" in runtime.task_prompt


@pytest.mark.asyncio
async def test_prepare_agent_runtime_context_keeps_skill_catalog_compact_by_default():
    llm = MockLLMClient(
        responses=[
            json.dumps(
                {
                    "goal": "Build parser",
                    "constraints": [],
                    "success_criteria": ["Done"],
                    "deliverables": ["Answer"],
                    "subtasks": ["Implement", "Verify"],
                }
            )
        ]
    )
    skill_tool = _SkillTool()
    history = [Message(role="user", content="Build parser with tests")]

    runtime = await _prepare_agent_runtime_context(llm, history, [skill_tool])

    assert len(runtime.relevant_skills) >= 1
    assert all(bool(item.get("detail_loaded")) is False for item in runtime.relevant_skills)
    assert skill_tool.read_calls == []


@pytest.mark.asyncio
async def test_prepare_agent_runtime_context_loads_skill_details_for_explicit_skill_intent():
    llm = MockLLMClient(
        responses=[
            json.dumps(
                {
                    "goal": "Explain skill",
                    "constraints": [],
                    "success_criteria": ["Done"],
                    "deliverables": ["Answer"],
                    "subtasks": ["Locate", "Explain"],
                }
            )
        ]
    )
    skill_tool = _SkillTool()
    history = [Message(role="user", content="请介绍 find-skills-0.1.0 这个技能的作用和使用方法")]

    runtime = await _prepare_agent_runtime_context(llm, history, [skill_tool])

    matched = [item for item in runtime.relevant_skills if item.get("name") == "find-skills-0.1.0"]
    assert matched
    assert bool(matched[0].get("detail_loaded")) is True
    assert "routing behavior" in str(matched[0].get("detail_excerpt", ""))
    assert "find-skills-0.1.0" in skill_tool.read_calls


@pytest.mark.asyncio
async def test_complete_chat_response_injects_skill_context_on_explicit_skill_query():
    llm = _RecordingLLM("ok")
    skill_tool = _SkillTool()
    history = [Message(role="user", content="What is find-skills-0.1.0 skill and how to use it?")]

    _ = await complete_chat_response(llm, history, tools=[skill_tool])

    system_messages = [msg.content for msg in llm.last_messages if msg.role == "system"]
    assert any("Skill catalog (name + usage scenario)" in msg for msg in system_messages)
    assert any("On-demand skill details" in msg for msg in system_messages)
    assert any("find-skills-0.1.0" in msg for msg in system_messages)


@pytest.mark.asyncio
async def test_complete_chat_response_keeps_catalog_only_for_generic_query():
    llm = _RecordingLLM("ok")
    skill_tool = _SkillTool()
    history = [Message(role="user", content="Implement parser and add tests")]

    _ = await complete_chat_response(llm, history, tools=[skill_tool])

    system_messages = [msg.content for msg in llm.last_messages if msg.role == "system"]
    assert any("Skill catalog (name + usage scenario)" in msg for msg in system_messages)
    assert all("On-demand skill details" not in msg for msg in system_messages)


@pytest.mark.asyncio
async def test_complete_chat_response_injects_task_memory_context(monkeypatch: pytest.MonkeyPatch):
    llm = _RecordingLLM("ok")
    history = [Message(role="user", content="build parser and verify")]
    monkeypatch.setattr("deepcode.chat_runtime._task_memory_store", lambda: _MemoryStoreStub())

    _ = await complete_chat_response(llm, history, tools=[])

    system_messages = [msg.content for msg in llm.last_messages if msg.role == "system"]
    assert any("Task memory context (retrieved)" in msg for msg in system_messages)
    assert any("parser and tests completed" in msg for msg in system_messages)


@pytest.mark.asyncio
async def test_prepare_agent_runtime_context_includes_relevant_memories(monkeypatch: pytest.MonkeyPatch):
    llm = MockLLMClient(
        responses=[
            json.dumps(
                {
                    "goal": "build parser",
                    "constraints": [],
                    "success_criteria": ["done"],
                    "deliverables": ["parser"],
                    "subtasks": ["implement", "verify"],
                }
            )
        ]
    )
    history = [Message(role="user", content="build parser and verify")]
    monkeypatch.setattr("deepcode.chat_runtime._task_memory_store", lambda: _MemoryStoreStub())

    runtime = await _prepare_agent_runtime_context(llm, history, tools=[])

    assert len(runtime.relevant_memories) >= 1
    assert "Relevant task memories" in runtime.task_prompt


@pytest.mark.asyncio
async def test_stream_agent_events_emits_prelude_and_final_answer():
    llm = MockLLMClient(
        responses=[
            _final_answer_response("stream-ok"),
        ]
    )
    history = [Message(role="user", content="Echo this")]
    tools = [_EchoTool()]

    events = []
    async for event in stream_agent_events(llm, history, tools, max_iterations=3):
        events.append(event)

    event_types = [str(item.get("type", "")) for item in events]
    assert "agent_context" in event_types
    assert event_types.count("reason") >= 3
    assert "final_answer" in event_types
    context_event = next(item for item in events if item.get("type") == "agent_context")
    assert isinstance(context_event.get("payload"), dict)
    assert "intent_route" in context_event.get("payload", {})
    final = next(item for item in events if item.get("type") == "final_answer")
    assert final.get("payload", {}).get("answer") == "stream-ok"


@pytest.mark.asyncio
async def test_complete_agent_response_falls_back_when_agent_answer_empty():
    llm = MockLLMClient(responses=["", "fallback-answer"])
    history = [Message(role="user", content="hello")]
    tools = [_EchoTool()]

    result = await complete_agent_response(llm, history, tools, max_iterations=1)

    assert result.answer == "fallback-answer"


@pytest.mark.asyncio
async def test_complete_agent_response_plan_only_returns_context():
    llm = MockLLMClient(
        responses=[
            json.dumps(
                {
                    "goal": "Build parser",
                    "constraints": [],
                    "success_criteria": ["Runs"],
                    "deliverables": ["script"],
                    "subtasks": ["analyze input", "write script", "verify output"],
                }
            )
        ]
    )
    history = [Message(role="user", content="Build parser")]
    tools = [_EchoTool()]

    result = await complete_agent_response(llm, history, tools, plan_only=True)

    assert result.success is True
    assert result.steps == []
    assert "Plan-Only mode" in result.answer
    assert isinstance(result.agent_context, dict)
    assert "intent_route" in result.agent_context
    assert "decomposed_task" in result.agent_context


@pytest.mark.asyncio
async def test_stream_agent_events_plan_only_short_circuits_execution():
    llm = MockLLMClient(
        responses=[
            json.dumps(
                {
                    "goal": "Draft migration",
                    "constraints": [],
                    "success_criteria": ["plan created"],
                    "deliverables": ["migration steps"],
                    "subtasks": ["inspect schema", "design migration", "validate rollback"],
                }
            )
        ]
    )
    history = [Message(role="user", content="Prepare DB migration")]
    tools = [_EchoTool()]

    events = []
    async for event in stream_agent_events(llm, history, tools, plan_only=True):
        events.append(event)

    event_types = [str(item.get("type", "")) for item in events]
    assert "agent_context" in event_types
    assert "final_answer" in event_types
    assert "function_call" not in event_types
    final = next(item for item in events if item.get("type") == "final_answer")
    assert "Plan-Only mode" in str(final.get("payload", {}).get("answer", ""))
