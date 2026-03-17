"""Unit tests for the BaseAgent ReAct loop."""

from __future__ import annotations

import json

import pytest

from deepcode.agents.base import AgentResponse, BaseAgent
from deepcode.llm.mock_client import MockLLMClient
from deepcode.tools.base import BaseTool, ToolResult


class _EchoTool(BaseTool):
    """A simple tool that echoes its input for testing."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echoes the provided text back"

    async def run(self, text: str = "", **kwargs) -> ToolResult:
        return ToolResult.ok(self.name, f"ECHO: {text}")


def _final_answer_response(answer: str) -> str:
    return json.dumps(
        {"thought": "I have the answer", "action": "final_answer", "action_input": {"answer": answer}}
    )


def _tool_response(tool: str, **inputs) -> str:
    return json.dumps(
        {"thought": "I should use a tool", "action": tool, "action_input": inputs}
    )


class TestBaseAgent:
    @pytest.mark.asyncio
    async def test_direct_final_answer(self):
        llm = MockLLMClient(responses=[_final_answer_response("42")])
        agent = BaseAgent(llm=llm)
        result = await agent.run("What is 6 * 7?")

        assert isinstance(result, AgentResponse)
        assert result.success is True
        assert result.answer == "42"
        assert len(result.steps) == 1

    @pytest.mark.asyncio
    async def test_tool_use_then_final_answer(self):
        responses = [
            _tool_response("echo", text="hello world"),
            _final_answer_response("Tool said: ECHO: hello world"),
        ]
        llm = MockLLMClient(responses=responses)
        agent = BaseAgent(llm=llm, tools=[_EchoTool()])
        result = await agent.run("Echo hello world")

        assert result.success is True
        assert len(result.steps) == 2
        assert result.steps[0].action == "echo"
        assert "ECHO: hello world" in result.steps[0].observation

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error_observation(self):
        responses = [
            _tool_response("nonexistent_tool"),
            _final_answer_response("Could not use the tool"),
        ]
        llm = MockLLMClient(responses=responses)
        agent = BaseAgent(llm=llm, tools=[])
        result = await agent.run("Use a nonexistent tool")

        # The agent should have continued after the failed tool call
        assert len(result.steps) >= 1
        assert "not found" in result.steps[0].observation.lower()

    @pytest.mark.asyncio
    async def test_max_iterations_respected(self):
        # Never gives a final answer
        tool_resp = _tool_response("echo", text="looping")
        llm = MockLLMClient(responses=[tool_resp] * 20)
        agent = BaseAgent(llm=llm, tools=[_EchoTool()], max_iterations=3)
        result = await agent.run("Loop forever")

        assert result.success is False
        assert "max iterations" in result.error.lower()
        assert len(result.steps) == 3

    @pytest.mark.asyncio
    async def test_unparseable_llm_response_becomes_final_answer(self):
        llm = MockLLMClient(responses=["This is plain text with no JSON"])
        agent = BaseAgent(llm=llm)
        result = await agent.run("What is the answer?")

        assert result.success is True
        assert result.answer == "This is plain text with no JSON"

    @pytest.mark.asyncio
    async def test_stream_run_yields_chunks(self):
        llm = MockLLMClient(responses=[_final_answer_response("Done!")])
        agent = BaseAgent(llm=llm)

        chunks = []
        async for chunk in agent.stream_run("Simple task"):
            chunks.append(chunk)

        full_text = "".join(chunks)
        assert len(chunks) > 0
        assert "Done!" in full_text
