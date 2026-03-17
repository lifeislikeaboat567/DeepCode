"""Base agent class implementing a ReAct (Reason + Act) loop."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, Field

from deepcode.llm.base import BaseLLMClient
from deepcode.logging_config import get_logger
from deepcode.memory.short_term import ShortTermMemory
from deepcode.tools.base import BaseTool, ToolResult

logger = get_logger(__name__)

_SYSTEM_PROMPT = """\
You are DeepCode Agent, an expert software engineering AI assistant.

You have access to the following tools:
{tool_descriptions}

To use a tool, respond with a JSON block using this exact format:
```json
{{
  "thought": "your reasoning about what to do next",
  "action": "tool_name",
  "action_input": {{
    "key": "value"
  }}
}}
```

When you have gathered enough information and are ready to give a final answer,
respond with:
```json
{{
  "thought": "I now have the answer",
  "action": "final_answer",
  "action_input": {{
    "answer": "your complete answer here"
  }}
}}
```

Always think step by step. Be concise and accurate.
"""


class AgentStep(BaseModel):
    """A single step in the agent's reasoning loop."""

    thought: str = Field(default="")
    action: str = Field(default="")
    action_input: dict[str, Any] = Field(default_factory=dict)
    observation: str = Field(default="")


class AgentResponse(BaseModel):
    """Final response from an agent run."""

    answer: str = Field(description="The agent's final answer")
    steps: list[AgentStep] = Field(
        default_factory=list,
        description="The reasoning steps taken to reach the answer",
    )
    success: bool = Field(default=True)
    error: str = Field(default="")
    code_artifacts: list[dict[str, str]] = Field(
        default_factory=list,
        description="Code files generated during the run",
    )


class BaseAgent:
    """ReAct-style agent that iterates through a Thought-Action-Observation loop.

    Args:
        llm: LLM client to use for generation.
        tools: List of tools the agent can invoke.
        max_iterations: Maximum number of Thought-Action-Observation cycles.
        system_prompt: Optional custom system prompt; overrides the default.
    """

    def __init__(
        self,
        llm: BaseLLMClient,
        tools: list[BaseTool] | None = None,
        max_iterations: int = 10,
        system_prompt: str | None = None,
    ) -> None:
        self._llm = llm
        self._tools: dict[str, BaseTool] = {t.name: t for t in (tools or [])}
        self._max_iterations = max_iterations
        self._system_prompt = system_prompt or self._build_system_prompt()
        self._memory = ShortTermMemory(system_prompt=self._system_prompt)

    def _build_system_prompt(self) -> str:
        """Construct the system prompt with current tool descriptions."""
        if not self._tools:
            tool_desc = "No tools available."
        else:
            lines = []
            for tool in self._tools.values():
                lines.append(f"- **{tool.name}**: {tool.description}")
            tool_desc = "\n".join(lines)

        return _SYSTEM_PROMPT.format(tool_descriptions=tool_desc)

    async def run(self, task: str) -> AgentResponse:
        """Execute *task* using the ReAct loop.

        Args:
            task: Natural language task description.

        Returns:
            An :class:`AgentResponse` with the final answer and all steps taken.
        """
        logger.info("Agent starting task", task=task[:100])
        self._memory.clear()
        self._memory.add("user", task)

        steps: list[AgentStep] = []
        code_artifacts: list[dict[str, str]] = []

        for iteration in range(self._max_iterations):
            logger.debug("Agent iteration", iteration=iteration)

            response = await self._llm.complete(self._memory.get_messages())
            raw_text = response.content.strip()

            # Parse the JSON action block from the LLM output
            step = self._parse_action(raw_text)
            steps.append(step)
            self._memory.add("assistant", raw_text)

            if step.action == "final_answer":
                answer = step.action_input.get("answer", raw_text)
                logger.info("Agent completed task", iterations=iteration + 1)
                return AgentResponse(
                    answer=answer,
                    steps=steps,
                    success=True,
                    code_artifacts=code_artifacts,
                )

            if step.action == "code_executor" or step.action == "file_manager":
                # Track code artifacts written
                if step.action == "file_manager" and step.action_input.get("action") == "write":
                    code_artifacts.append(
                        {
                            "filename": step.action_input.get("path", "output.py"),
                            "content": step.action_input.get("content", ""),
                        }
                    )

            # Execute tool
            observation = await self._execute_tool(step)
            step.observation = observation
            self._memory.add("user", f"Observation: {observation}")

        # Max iterations reached
        logger.warning("Agent reached max iterations", max=self._max_iterations)
        return AgentResponse(
            answer="Could not complete the task within the maximum number of iterations.",
            steps=steps,
            success=False,
            error="Max iterations reached",
            code_artifacts=code_artifacts,
        )

    async def stream_run(self, task: str) -> AsyncIterator[str]:
        """Stream the agent's reasoning process as text chunks.

        Args:
            task: Natural language task description.

        Yields:
            Text chunks describing the agent's thought process and actions.
        """
        yield f"🤔 Starting task: {task}\n\n"
        self._memory.clear()
        self._memory.add("user", task)

        for iteration in range(self._max_iterations):
            yield f"**Step {iteration + 1}**\n"

            response = await self._llm.complete(self._memory.get_messages())
            raw_text = response.content.strip()

            step = self._parse_action(raw_text)
            self._memory.add("assistant", raw_text)

            if step.thought:
                yield f"💭 Thought: {step.thought}\n"

            if step.action == "final_answer":
                answer = step.action_input.get("answer", raw_text)
                yield f"\n✅ **Final Answer:**\n{answer}\n"
                return

            yield f"🔧 Action: `{step.action}`\n"

            observation = await self._execute_tool(step)
            step.observation = observation
            self._memory.add("user", f"Observation: {observation}")

            # Truncate long observations in the stream
            display_obs = observation[:500] + "..." if len(observation) > 500 else observation
            yield f"👁️ Observation: {display_obs}\n\n"

        yield "⚠️ Reached maximum iterations without a final answer.\n"

    async def _execute_tool(self, step: AgentStep) -> str:
        """Invoke the tool specified in *step* and return its observation string.

        Args:
            step: Agent step containing the action name and input.

        Returns:
            String observation to feed back to the LLM.
        """
        tool = self._tools.get(step.action)
        if tool is None:
            available = ", ".join(self._tools) or "none"
            return f"Tool '{step.action}' not found. Available: {available}"

        try:
            result: ToolResult = await tool.run(**step.action_input)
        except Exception as exc:
            logger.error("Tool execution raised", tool=step.action, error=str(exc))
            return f"Tool execution error: {exc}"

        if result.success:
            return result.output or "(no output)"
        return f"Error: {result.error}"

    @staticmethod
    def _parse_action(text: str) -> AgentStep:
        """Extract a JSON action block from the LLM output.

        Args:
            text: Raw LLM response text.

        Returns:
            Parsed :class:`AgentStep`; falls back to a ``final_answer`` step
            if no valid JSON is found.
        """
        # Try to extract JSON from a fenced code block first
        pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
        match = re.search(pattern, text, re.DOTALL)
        json_str = match.group(1) if match else text

        # Fall back to searching for the first { ... } in the text
        if not match:
            brace_match = re.search(r"\{.*\}", text, re.DOTALL)
            json_str = brace_match.group(0) if brace_match else text

        try:
            data = json.loads(json_str)
            return AgentStep(
                thought=data.get("thought", ""),
                action=data.get("action", "final_answer"),
                action_input=data.get("action_input", {"answer": text}),
            )
        except (json.JSONDecodeError, ValueError):
            # Treat unparseable responses as a direct final answer
            return AgentStep(
                thought="",
                action="final_answer",
                action_input={"answer": text},
            )
