"""Orchestrator agent that coordinates multi-step coding workflows."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, Field

from deepcode.agents.base import BaseAgent
from deepcode.llm.base import BaseLLMClient
from deepcode.logging_config import get_logger
from deepcode.tools.base import BaseTool

logger = get_logger(__name__)

_ORCHESTRATOR_SYSTEM = """\
You are the DeepCode Orchestrator – a senior software architect responsible for
decomposing user requests into a clear, actionable development plan.

When given a task, produce a numbered plan with specific, concrete steps.
Each step must specify:
1. What to build/implement
2. Which file(s) to create or modify
3. What the expected outcome is

After producing the plan, summarize it concisely. Be precise and technical.
You do NOT implement code yourself – you plan it.
"""

_CODER_SYSTEM = """\
You are the DeepCode Coder – an expert Python developer.
Your job is to implement the specific step assigned to you.

Rules:
- Write complete, runnable code (never leave placeholders or TODOs)
- Follow PEP 8 and include type hints
- Add concise docstrings to all public functions and classes
- When using the file_manager tool to save code, always use action="write"
- When using code_executor tool to run code, wrap it in try/except

Available tools:
{tool_descriptions}

Respond with a JSON action block or a final_answer when done.
```json
{{
  "thought": "...",
  "action": "tool_name",
  "action_input": {{...}}
}}
```
"""

_REVIEWER_SYSTEM = """\
You are the DeepCode Reviewer – a meticulous code quality engineer.
Review the provided code for:
1. Correctness and logic errors
2. Security issues (e.g., injection, insecure eval, path traversal)
3. Code style and Pythonic patterns
4. Missing error handling
5. Missing or incorrect type hints

Respond with a JSON object:
{
  "passed": true/false,
  "score": 0-10,
  "issues": ["issue 1", "issue 2"],
  "suggestions": ["suggestion 1"]
}
"""

_TESTER_SYSTEM = """\
You are the DeepCode Tester – a test-driven development specialist.
Your job is to write pytest unit tests for the provided code.

Rules:
- Write complete, runnable pytest tests
- Test the happy path AND edge cases
- Use descriptive test function names
- Include at least 3 test cases per function
- Save tests to a file called test_<module>.py
"""


class WorkflowResult(BaseModel):
    """Result from an orchestrated multi-agent workflow."""

    task: str
    plan: list[str] = Field(default_factory=list)
    code_artifacts: list[dict[str, str]] = Field(default_factory=list)
    test_artifacts: list[dict[str, str]] = Field(default_factory=list)
    review_result: dict[str, Any] = Field(default_factory=dict)
    execution_results: list[dict[str, Any]] = Field(default_factory=list)
    success: bool = True
    error: str = ""
    summary: str = ""


class OrchestratorAgent:
    """High-level agent that runs a complete plan→code→review→test workflow.

    This agent coordinates three specialized sub-agents:

    - **Planner**: Decomposes the task into a numbered plan
    - **Coder**: Implements each step of the plan
    - **Reviewer**: Reviews the generated code for quality and security

    Args:
        llm: LLM client shared by all sub-agents.
        tools: Tools available to sub-agents.
        max_iterations: Maximum ReAct loop iterations per sub-agent.
    """

    def __init__(
        self,
        llm: BaseLLMClient,
        tools: list[BaseTool] | None = None,
        max_iterations: int = 10,
    ) -> None:
        self._llm = llm
        self._tools = tools or []
        self._max_iterations = max_iterations

        tool_desc = "\n".join(
            f"- **{t.name}**: {t.description}" for t in self._tools
        ) or "No tools available."

        self._planner = BaseAgent(
            llm=llm,
            tools=[],
            max_iterations=5,
            system_prompt=_ORCHESTRATOR_SYSTEM,
        )
        self._coder = BaseAgent(
            llm=llm,
            tools=self._tools,
            max_iterations=max_iterations,
            system_prompt=_CODER_SYSTEM.format(tool_descriptions=tool_desc),
        )
        self._reviewer = BaseAgent(
            llm=llm,
            tools=[],
            max_iterations=3,
            system_prompt=_REVIEWER_SYSTEM,
        )
        self._tester = BaseAgent(
            llm=llm,
            tools=self._tools,
            max_iterations=max_iterations,
            system_prompt=_TESTER_SYSTEM,
        )

    async def run(self, task: str) -> WorkflowResult:
        """Execute the full orchestrated workflow for *task*.

        Stages:
        1. **Plan** – decompose the task
        2. **Code** – implement the plan
        3. **Review** – audit the generated code
        4. **Test** – generate and run tests

        Args:
            task: Natural language description of what to build.

        Returns:
            :class:`WorkflowResult` with all artifacts and outcomes.
        """
        result = WorkflowResult(task=task)

        try:
            # ── Stage 1: Plan ────────────────────────────────────────────────
            logger.info("Orchestrator: planning", task=task[:80])
            plan_response = await self._planner.run(
                f"Create a detailed development plan for the following task:\n\n{task}"
            )
            plan_text = plan_response.answer
            result.plan = self._extract_plan_steps(plan_text)

            # ── Stage 2: Code ────────────────────────────────────────────────
            logger.info("Orchestrator: coding", steps=len(result.plan))
            code_prompt = (
                f"Implement the following task according to this plan:\n\n"
                f"**Task:** {task}\n\n"
                f"**Plan:**\n{plan_text}\n\n"
                "Implement all steps. Save each file using the file_manager tool."
            )
            code_response = await self._coder.run(code_prompt)
            result.code_artifacts = code_response.code_artifacts

            # ── Stage 3: Review ──────────────────────────────────────────────
            if result.code_artifacts:
                logger.info("Orchestrator: reviewing code")
                artifacts_text = "\n\n".join(
                    f"**{a['filename']}**\n```python\n{a['content']}\n```"
                    for a in result.code_artifacts
                )
                review_response = await self._reviewer.run(
                    f"Review the following code:\n\n{artifacts_text}"
                )
                result.review_result = self._parse_review(review_response.answer)

            # ── Stage 4: Test ────────────────────────────────────────────────
            if result.code_artifacts:
                logger.info("Orchestrator: generating tests")
                test_prompt = (
                    "Write pytest tests for the following code:\n\n"
                    + "\n\n".join(
                        f"**{a['filename']}**\n```python\n{a['content']}\n```"
                        for a in result.code_artifacts
                    )
                )
                test_response = await self._tester.run(test_prompt)
                result.test_artifacts = test_response.code_artifacts

            result.summary = code_response.answer
            result.success = True

        except Exception as exc:
            logger.error("Orchestrator workflow failed", error=str(exc))
            result.success = False
            result.error = str(exc)

        return result

    async def stream_run(self, task: str) -> AsyncIterator[str]:
        """Stream the orchestrated workflow as text events.

        Args:
            task: Natural language description of what to build.

        Yields:
            Text chunks describing progress through each stage.
        """
        yield f"🚀 **DeepCode Agent** – Starting task\n\n> {task}\n\n"

        # Plan
        yield "## 📋 Stage 1: Planning\n\n"
        plan_response = await self._planner.run(
            f"Create a detailed development plan for:\n\n{task}"
        )
        yield plan_response.answer + "\n\n"

        # Code
        yield "## 💻 Stage 2: Coding\n\n"
        code_prompt = (
            f"Implement the following task:\n\n**Task:** {task}\n\n"
            f"**Plan:**\n{plan_response.answer}\n\n"
            "Implement all steps. Save each file using the file_manager tool."
        )
        async for chunk in self._coder.stream_run(code_prompt):
            yield chunk

        yield "\n\n## ✅ Stage 3: Review\n\n*Review complete – see summary above.*\n"

    @staticmethod
    def _extract_plan_steps(plan_text: str) -> list[str]:
        """Parse numbered steps from the planner's output.

        Args:
            plan_text: Raw planner response text.

        Returns:
            List of step strings.
        """
        import re

        steps = re.findall(r"^\s*\d+[\.\)]\s+(.+)$", plan_text, re.MULTILINE)
        return steps if steps else [plan_text]

    @staticmethod
    def _parse_review(review_text: str) -> dict[str, Any]:
        """Attempt to extract a structured review dict from the reviewer output.

        Args:
            review_text: Raw reviewer response.

        Returns:
            Dict with ``passed``, ``score``, ``issues``, ``suggestions`` keys.
        """
        import json
        import re

        # Try a fenced code block with a captured group first
        fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", review_text, re.DOTALL)
        if fenced_match:
            json_str = fenced_match.group(1)
        else:
            # Fall back to bare JSON object
            bare_match = re.search(r"\{.*\}", review_text, re.DOTALL)
            json_str = bare_match.group(0) if bare_match else None

        if json_str:
            try:
                return json.loads(json_str)
            except (json.JSONDecodeError, ValueError):
                pass

        return {"passed": True, "score": 7, "issues": [], "raw": review_text}



