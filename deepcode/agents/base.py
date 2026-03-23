"""Base agent class implementing a ReAct (Reason + Act) loop."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, Field

from deepcode.extensions import HookContext, HookEvent, HookManager
from deepcode.governance import ApprovalStore, PolicyEngine
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

If tools named `skill_registry` or `mcp_service` are available, proactively use
them when they can improve solution quality (for example: discovering reusable
skills, checking configured MCP services, or fetching external MCP context).

Always think step by step. Be concise and accurate.
"""


class AgentStep(BaseModel):
    """A single step in the agent's reasoning loop."""

    thought: str = Field(default="")
    action: str = Field(default="")
    action_input: dict[str, Any] = Field(default_factory=dict)
    observation: str = Field(default="")
    tool_success: bool | None = Field(default=None)
    tool_metadata: dict[str, Any] = Field(default_factory=dict)


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
    agent_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured context (intent/decomposition/capabilities) for this run",
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
        hook_manager: HookManager | None = None,
        policy_engine: PolicyEngine | None = None,
        approval_store: ApprovalStore | None = None,
    ) -> None:
        self._llm = llm
        self._tools: dict[str, BaseTool] = {t.name: t for t in (tools or [])}
        self._max_iterations = max_iterations
        self._system_prompt = system_prompt or self._build_system_prompt()
        self._memory = ShortTermMemory(system_prompt=self._system_prompt)
        self._hook_manager = hook_manager or HookManager()
        self._policy_engine = policy_engine or PolicyEngine()
        self._approval_store = approval_store or ApprovalStore()

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
        await self._hook_manager.emit(HookContext(event=HookEvent.TASK_STARTED, task=task))
        self._memory.clear()
        self._memory.add("user", task)

        steps: list[AgentStep] = []
        code_artifacts: list[dict[str, str]] = []

        for iteration in range(self._max_iterations):
            logger.debug("Agent iteration", iteration=iteration)

            await self._hook_manager.emit(
                HookContext(event=HookEvent.BEFORE_LLM, task=task, iteration=iteration)
            )
            response = await self._llm.complete(self._memory.get_messages())
            raw_text = response.content.strip()
            await self._hook_manager.emit(
                HookContext(
                    event=HookEvent.AFTER_LLM,
                    task=task,
                    iteration=iteration,
                    payload={"raw_text": raw_text},
                )
            )

            # Parse the JSON action block from the LLM output
            step = self._parse_action(raw_text)
            steps.append(step)
            self._memory.add("assistant", raw_text)

            if step.action == "final_answer":
                answer = self._extract_final_answer(step, raw_text)
                if not answer:
                    logger.warning(
                        "Agent got empty final answer, retrying",
                        iteration=iteration,
                        raw_text_length=len(raw_text),
                    )
                    # Some providers may return an empty content turn; retry.
                    self._memory.add(
                        "user",
                        json.dumps(
                            {
                                "type": "assistant_empty_response",
                                "message": "Assistant returned an empty final answer; retry with a concrete final_answer.",
                            },
                            ensure_ascii=False,
                        ),
                    )
                    continue

                logger.info("Agent completed task", iterations=iteration + 1)
                await self._hook_manager.emit(
                    HookContext(
                        event=HookEvent.TASK_FINISHED,
                        task=task,
                        iteration=iteration,
                        payload={"success": True, "answer": answer},
                    )
                )
                return AgentResponse(
                    answer=answer,
                    steps=steps,
                    success=True,
                    code_artifacts=code_artifacts,
                )

            # Execute tool
            result = await self._execute_tool(step)
            step.tool_success = result.success
            step.tool_metadata = dict(result.metadata)
            step.observation = self._format_observation(result)
            code_artifacts.extend(self._extract_code_artifacts(step, result))
            function_result = {
                "type": "function_result",
                "name": step.action,
                "success": bool(result.success),
                "content": step.observation,
                "metadata": result.metadata,
            }
            self._memory.add("user", json.dumps(function_result, ensure_ascii=False))

        # Max iterations reached
        logger.warning("Agent reached max iterations", max=self._max_iterations)
        await self._hook_manager.emit(
            HookContext(
                event=HookEvent.TASK_FINISHED,
                task=task,
                iteration=self._max_iterations,
                payload={"success": False, "error": "Max iterations reached"},
            )
        )
        return AgentResponse(
            answer="Could not complete the task within the maximum number of iterations.",
            steps=steps,
            success=False,
            error="Max iterations reached",
            code_artifacts=code_artifacts,
        )

    async def stream_run_events(self, task: str) -> AsyncIterator[dict[str, Any]]:
        """Stream structured ReAct events for Agent mode consumers.

        Event format:
            {
                "type": "reason|function_call|observation|final_answer|warning|start",
                "payload": {...}
            }
        """
        yield {
            "type": "start",
            "payload": {
                "task": task,
            },
        }

        self._memory.clear()
        self._memory.add("user", task)

        for iteration in range(self._max_iterations):
            step_index = iteration + 1

            response = await self._llm.complete(self._memory.get_messages())
            raw_text = response.content.strip()

            step = self._parse_action(raw_text)
            self._memory.add("assistant", raw_text)

            if step.thought:
                yield {
                    "type": "reason",
                    "payload": {
                        "step": step_index,
                        "content": step.thought,
                    },
                }

            if step.action == "final_answer":
                answer = self._extract_final_answer(step, raw_text)
                if not answer:
                    logger.warning(
                        "Agent stream got empty final answer, retrying",
                        step=step_index,
                        raw_text_length=len(raw_text),
                    )
                    yield {
                        "type": "warning",
                        "payload": {
                            "step": step_index,
                            "message": "Model returned an empty final answer; retrying.",
                        },
                    }
                    self._memory.add(
                        "user",
                        json.dumps(
                            {
                                "type": "assistant_empty_response",
                                "message": "Assistant returned an empty final answer; retry with a concrete final_answer.",
                            },
                            ensure_ascii=False,
                        ),
                    )
                    continue

                yield {
                    "type": "final_answer",
                    "payload": {
                        "step": step_index,
                        "answer": answer,
                    },
                }
                return

            yield {
                "type": "function_call",
                "payload": {
                    "step": step_index,
                    "name": step.action,
                    "arguments": dict(step.action_input),
                },
            }

            result = await self._execute_tool(step)
            step.tool_success = result.success
            step.tool_metadata = dict(result.metadata)
            step.observation = self._format_observation(result)
            function_result = {
                "type": "function_result",
                "name": step.action,
                "success": bool(result.success),
                "content": step.observation,
                "metadata": result.metadata,
            }
            self._memory.add("user", json.dumps(function_result, ensure_ascii=False))

            yield {
                "type": "observation",
                "payload": {
                    "step": step_index,
                    "tool_name": step.action,
                    "success": bool(result.success),
                    "content": step.observation,
                    "metadata": dict(result.metadata),
                },
            }

        yield {
            "type": "warning",
            "payload": {
                "message": "Reached maximum iterations without a final answer.",
                "max_iterations": self._max_iterations,
            },
        }

    async def stream_run(self, task: str) -> AsyncIterator[str]:
        """Stream compatibility text chunks derived from structured events."""
        async for event in self.stream_run_events(task):
            event_type = str(event.get("type", ""))
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}

            if event_type == "start":
                yield f"🤔 Starting task: {payload.get('task', task)}\n\n"
                continue

            if event_type == "reason":
                step = payload.get("step", "")
                content = payload.get("content", "")
                yield f"**Step {step}**\n💭 Thought: {content}\n"
                continue

            if event_type == "function_call":
                name = str(payload.get("name", ""))
                arguments = payload.get("arguments", {})
                action_input_text = ""
                if isinstance(arguments, dict) and arguments:
                    try:
                        action_input_text = json.dumps(arguments, ensure_ascii=False)
                    except (TypeError, ValueError):
                        action_input_text = str(arguments)

                if action_input_text:
                    yield f"🔧 Function Call: `{name}` with `{action_input_text}`\n"
                else:
                    yield f"🔧 Function Call: `{name}`\n"
                continue

            if event_type == "observation":
                content = str(payload.get("content", ""))
                display_obs = content[:500] + "..." if len(content) > 500 else content
                yield f"👁️ Observation: {display_obs}\n\n"
                continue

            if event_type == "final_answer":
                answer = str(payload.get("answer", ""))
                yield f"\n✅ **Final Answer:**\n{answer}\n"
                continue

            if event_type == "warning":
                message = str(payload.get("message", "")) or "Reached maximum iterations without a final answer."
                yield f"⚠️ {message}\n"

    async def _execute_tool(self, step: AgentStep) -> ToolResult:
        """Invoke the tool specified in *step* and return the tool result.

        Args:
            step: Agent step containing the action name and input.

        Returns:
            Structured :class:`ToolResult` for observation and artifact extraction.
        """
        tool = self._tools.get(step.action)
        if tool is None:
            available = ", ".join(self._tools) or "none"
            return ToolResult.fail(
                step.action,
                f"Tool '{step.action}' not found. Available: {available}",
            )

        decision = self._policy_engine.evaluate(step.action, dict(step.action_input))
        if decision.decision == "deny":
            rule = decision.matched_rule.name if decision.matched_rule else "unnamed-rule"
            return ToolResult.fail(
                step.action,
                f"Policy denied tool call '{step.action}'. Rule: {rule}",
                policy_decision=decision.decision,
                policy_reason=decision.reason,
                policy_rule_id=decision.matched_rule.id if decision.matched_rule else "",
            )

        if decision.decision == "ask":
            approval_id = str(step.action_input.get("approval_request_id", "") or "").strip()
            if approval_id:
                existing = self._approval_store.get(approval_id)
                if existing is not None and existing.status == "approved" and existing.tool_name == step.action:
                    logger.info("Approved tool call bypassing ask policy", tool=step.action, approval_id=approval_id)
                else:
                    return ToolResult.fail(
                        step.action,
                        (
                            f"Invalid or non-approved approval request for tool '{step.action}': "
                            f"{approval_id}"
                        ),
                        policy_decision=decision.decision,
                        policy_reason=decision.reason,
                        approval_request_id=approval_id,
                    )
            else:
                approval = self._approval_store.create(
                    tool_name=step.action,
                    action_input=dict(step.action_input),
                    reason=decision.reason,
                    rule_id=decision.matched_rule.id if decision.matched_rule else "",
                )
                rule = decision.matched_rule.name if decision.matched_rule else "unnamed-rule"
                return ToolResult.fail(
                    step.action,
                    (
                        f"Policy requires approval before tool call '{step.action}'. "
                        f"Rule: {rule}. Approval request: {approval.id}"
                    ),
                    policy_decision=decision.decision,
                    policy_reason=decision.reason,
                    policy_rule_id=decision.matched_rule.id if decision.matched_rule else "",
                    approval_request_id=approval.id,
                )

        try:
            await self._hook_manager.emit(
                HookContext(
                    event=HookEvent.BEFORE_TOOL,
                    action=step.action,
                    payload={"action_input": step.action_input},
                )
            )
            result: ToolResult = await tool.run(**step.action_input)
            await self._hook_manager.emit(
                HookContext(
                    event=HookEvent.AFTER_TOOL,
                    action=step.action,
                    payload={
                        "success": result.success,
                        "output": result.output,
                        "error": result.error,
                    },
                )
            )
        except Exception as exc:
            logger.error("Tool execution raised", tool=step.action, error=str(exc))
            return ToolResult.fail(step.action, f"Tool execution error: {exc}")

        return result

    @staticmethod
    def _format_observation(result: ToolResult) -> str:
        if result.success:
            return result.output or "(no output)"
        return f"Error: {result.error}"

    @staticmethod
    def _extract_final_answer(step: AgentStep, raw_text: str) -> str:
        """Derive a best-effort final answer from action payload or raw text."""
        candidates = [
            step.action_input.get("answer"),
            step.action_input.get("final_answer"),
            step.action_input.get("response"),
            step.action_input.get("content"),
            raw_text,
        ]
        for value in candidates:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    def _extract_code_artifacts(self, step: AgentStep, result: ToolResult) -> list[dict[str, str]]:
        if not result.success:
            return []

        if step.action == "file_manager" and step.action_input.get("action") == "write":
            path = str(step.action_input.get("path") or result.metadata.get("path") or "output.py")
            content = str(step.action_input.get("content") or "")
            return [
                {
                    "filename": path,
                    "content": content,
                    "language": self._guess_language(path),
                    "kind": "file",
                    "tool_name": step.action,
                }
            ]

        if step.action == "script_runner":
            path = str(step.action_input.get("path") or result.metadata.get("path") or "script.py")
            content = str(step.action_input.get("content") or "")
            language = str(step.action_input.get("language") or result.metadata.get("language") or "python")
            return [
                {
                    "filename": path,
                    "content": content,
                    "language": language,
                    "kind": "script",
                    "tool_name": step.action,
                }
            ]

        return []

    @staticmethod
    def _guess_language(path: str) -> str:
        suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        mapping = {
            "py": "python",
            "js": "javascript",
            "ts": "typescript",
            "tsx": "tsx",
            "jsx": "jsx",
            "json": "json",
            "md": "markdown",
            "yml": "yaml",
            "yaml": "yaml",
            "toml": "toml",
            "html": "html",
            "css": "css",
            "sh": "bash",
            "ps1": "powershell",
            "sql": "sql",
        }
        return mapping.get(suffix, "text")

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

        def _coerce_arguments(raw_args: Any) -> dict[str, Any]:
            if isinstance(raw_args, str):
                try:
                    parsed_args = json.loads(raw_args)
                    if isinstance(parsed_args, dict):
                        return parsed_args
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
                return {"input": raw_args}
            if isinstance(raw_args, dict):
                return raw_args
            return {}

        try:
            data = json.loads(json_str)
            if not isinstance(data, dict):
                return AgentStep(
                    thought="",
                    action="final_answer",
                    action_input={"answer": text},
                )

            function_call = data.get("function_call")
            if isinstance(function_call, dict):
                arguments = _coerce_arguments(function_call.get("arguments", {}))

                return AgentStep(
                    thought=data.get("thought", ""),
                    action=str(function_call.get("name", "") or "final_answer"),
                    action_input=arguments,
                )

            if "name" in data and "arguments" in data:
                return AgentStep(
                    thought=data.get("thought", ""),
                    action=str(data.get("name", "") or "final_answer"),
                    action_input=_coerce_arguments(data.get("arguments", {})),
                )

            tool_calls = data.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                first_call = tool_calls[0]
                if isinstance(first_call, dict):
                    function_payload = first_call.get("function") or first_call
                    if isinstance(function_payload, dict):
                        return AgentStep(
                            thought=data.get("thought", ""),
                            action=str(function_payload.get("name", "") or "final_answer"),
                            action_input=_coerce_arguments(function_payload.get("arguments", {})),
                        )

            action_input = data.get("action_input", {"answer": text})
            if not isinstance(action_input, dict):
                action_input = {"input": str(action_input)}

            return AgentStep(
                thought=data.get("thought", ""),
                action=str(data.get("action", "final_answer") or "final_answer"),
                action_input=action_input,
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            # Treat unparseable responses as a direct final answer
            return AgentStep(
                thought="",
                action="final_answer",
                action_input={"answer": text},
            )
