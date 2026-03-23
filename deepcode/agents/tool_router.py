"""Deterministic tool routing helpers for the high-agency orchestrator."""

from __future__ import annotations

from typing import Any

from deepcode.agents.task_state import NextAction, PlanStep
from deepcode.tools.base import BaseTool

_SCRIPT_KEYWORDS = {
    "batch",
    "parse",
    "extract",
    "transform",
    "convert",
    "migrate",
    "validate",
    "scrape",
    "report",
    "analyze",
    "automation",
    "generate dataset",
}

_READ_KEYWORDS = {"read", "inspect", "list", "open", "tree", "scan"}
_SEARCH_KEYWORDS = {"search", "find", "grep", "lookup"}
_EXEC_KEYWORDS = {"run", "execute", "command", "shell"}
_TEST_KEYWORDS = {"test", "pytest", "case", "verify runtime"}


class ToolRouter:
    """Choose a deterministic default route before LLM refinement."""

    def __init__(self, tools_by_name: dict[str, BaseTool]) -> None:
        self._tools_by_name = tools_by_name

    def route(self, step: PlanStep) -> NextAction:
        """Return the best default route for a plan step."""
        if step.tool_name and step.tool_name in self._tools_by_name:
            return NextAction(
                type=step.action_type,
                tool_name=step.tool_name,
                reason="Plan step already specifies an available tool",
                input=dict(step.inputs),
            )

        tool_name = self._select_tool_name(step)
        route_input = self._build_default_input(step, tool_name)
        return NextAction(
            type=step.action_type,
            tool_name=tool_name,
            reason="Deterministic routing based on action type and step intent",
            input=route_input,
        )

    def _select_tool_name(self, step: PlanStep) -> str | None:
        normalized = self._combined_text(step)
        if step.action_type in {"read", "search"}:
            return "file_manager" if "file_manager" in self._tools_by_name else None
        if step.action_type == "write":
            return "file_manager" if "file_manager" in self._tools_by_name else None
        if step.action_type in {"exec", "test", "verify"}:
            if self._is_script_worthy(normalized) and "script_runner" in self._tools_by_name:
                return "script_runner"
            if "code_executor" in self._tools_by_name:
                return "code_executor"
            if "shell" in self._tools_by_name:
                return "shell"
            return None
        if step.action_type == "code":
            if self._is_script_worthy(normalized) and "script_runner" in self._tools_by_name:
                return "script_runner"
            if "file_manager" in self._tools_by_name:
                return "file_manager"
            return None
        return None

    def _build_default_input(self, step: PlanStep, tool_name: str | None) -> dict[str, Any]:
        if tool_name == "file_manager":
            text = self._combined_text(step)
            if step.action_type == "read":
                if any(token in text for token in {"tree", "structure", "layout"}):
                    return {"action": "tree", "path": ".", "max_depth": 3}
                if any(token in text for token in _READ_KEYWORDS):
                    return {"action": "list", "path": "."}
            if step.action_type == "search":
                return {"action": "tree", "path": ".", "max_depth": 4}
            return dict(step.inputs)
        return dict(step.inputs)

    @staticmethod
    def _combined_text(step: PlanStep) -> str:
        return f"{step.title} {step.purpose} {step.expected_output} {step.verification_method}".lower()

    @staticmethod
    def _is_script_worthy(text: str) -> bool:
        return any(keyword in text for keyword in _SCRIPT_KEYWORDS)

    @staticmethod
    def infer_action_type(step_text: str) -> str:
        """Infer an action type from free-form step text."""
        normalized = step_text.lower()
        if any(word in normalized for word in _READ_KEYWORDS):
            return "read"
        if any(word in normalized for word in _SEARCH_KEYWORDS):
            return "search"
        if any(word in normalized for word in _EXEC_KEYWORDS):
            return "exec"
        if any(word in normalized for word in {"write", "edit", "create file", "patch"}):
            return "write"
        if any(word in normalized for word in _TEST_KEYWORDS):
            return "test"
        if any(word in normalized for word in {"verify", "validate", "check"}):
            return "verify"
        if any(word in normalized for word in {"ask", "confirm", "clarify"}):
            return "ask"
        return "code"
