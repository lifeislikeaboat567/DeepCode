"""Orchestrator agent with explicit planning, execution, validation and reflection."""

from __future__ import annotations

import json
from pathlib import Path
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, Field

from deepcode.agents.base import BaseAgent
from deepcode.agents.prompt_layers import (
    get_coder_system_prompt,
    get_finalizer_system_prompt,
    get_normalizer_system_prompt,
    get_planner_system_prompt,
    get_reflection_system_prompt,
    get_reviewer_system_prompt,
    get_router_system_prompt,
    get_tester_system_prompt,
    get_validator_system_prompt,
)
from deepcode.agents.reflection import classify_failure_output, fallback_reflection
from deepcode.agents.task_state import (
    AgentTaskState,
    ArtifactRecord,
    ErrorRecord,
    NextAction,
    Observation,
    PlanStep,
    ReflectionRecord,
)
from deepcode.agents.tool_router import ToolRouter
from deepcode.governance import PolicyEngine
from deepcode.llm.base import BaseLLMClient
from deepcode.logging_config import get_logger
from deepcode.tools.base import BaseTool

logger = get_logger(__name__)

_HIGH_RISK_KEYWORDS = {
    "delete",
    "drop",
    "destroy",
    "wipe",
    "truncate",
    "overwrite",
    "push",
    "publish",
    "deploy",
    "shutdown",
    "send externally",
}


class WorkflowResult(BaseModel):
    """Result from an orchestrated high-agency workflow."""

    task: str
    plan: list[str] = Field(default_factory=list)
    code_artifacts: list[dict[str, str]] = Field(default_factory=list)
    test_artifacts: list[dict[str, str]] = Field(default_factory=list)
    review_result: dict[str, Any] = Field(default_factory=dict)
    execution_results: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    reflections: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    task_state: dict[str, Any] = Field(default_factory=dict)
    success: bool = True
    error: str = ""
    summary: str = ""


class OrchestratorAgent:
    """High-agency orchestrator with explicit normalize-plan-execute-validate loop."""

    def __init__(
        self,
        llm: BaseLLMClient,
        tools: list[BaseTool] | None = None,
        max_iterations: int = 10,
        allow_high_risk_actions: bool = False,
        policy_engine: PolicyEngine | None = None,
    ) -> None:
        self._llm = llm
        self._tools = tools or []
        self._tools_by_name = {tool.name: tool for tool in self._tools}
        self._tool_router = ToolRouter(self._tools_by_name)
        self._max_iterations = max_iterations
        self._allow_high_risk_actions = allow_high_risk_actions
        self._policy_engine = policy_engine or PolicyEngine()

        tool_desc = "\n".join(
            f"- **{t.name}**: {t.description}" for t in self._tools
        ) or "No tools available."

        self._normalizer = BaseAgent(llm=llm, tools=[], max_iterations=3, system_prompt=get_normalizer_system_prompt())
        self._planner = BaseAgent(llm=llm, tools=[], max_iterations=4, system_prompt=get_planner_system_prompt())
        self._router = BaseAgent(llm=llm, tools=[], max_iterations=3, system_prompt=get_router_system_prompt())
        self._coder = BaseAgent(
            llm=llm,
            tools=self._tools,
            max_iterations=max_iterations,
            system_prompt=get_coder_system_prompt(tool_desc),
        )
        self._validator = BaseAgent(llm=llm, tools=[], max_iterations=3, system_prompt=get_validator_system_prompt())
        self._reflection = BaseAgent(llm=llm, tools=[], max_iterations=3, system_prompt=get_reflection_system_prompt())
        self._reviewer = BaseAgent(llm=llm, tools=[], max_iterations=3, system_prompt=get_reviewer_system_prompt())
        self._tester = BaseAgent(llm=llm, tools=self._tools, max_iterations=max_iterations, system_prompt=get_tester_system_prompt())
        self._finalizer = BaseAgent(llm=llm, tools=[], max_iterations=3, system_prompt=get_finalizer_system_prompt())

    async def run(self, task: str) -> WorkflowResult:
        """Execute a task using explicit stateful loops and validation."""
        result = WorkflowResult(task=task)
        state = AgentTaskState(goal=task, risk_level=self._classify_risk(task))

        try:
            state = await self._normalize_task(task, state)
            state.status = "running"

            if state.risk_level == "high" and not self._allow_high_risk_actions:
                msg = (
                    "Task classified as high risk. Explicit approval is required before "
                    "destructive or irreversible actions."
                )
                state.status = "needs_input"
                state.errors.append(
                    ErrorRecord(
                        step_id=None,
                        category="permission_issue",
                        message=msg,
                    )
                )
                result.success = False
                result.error = msg
                result.summary = msg
                return self._finalize_result(result, state)

            await self._observe_environment(state)
            await self._build_plan(state)
            result.plan = [step.title for step in state.plan]

            step_budget = max(1, state.budget.max_steps)
            tool_budget = max(1, state.budget.max_tool_calls)
            tool_calls = 0
            attempts: dict[str, int] = {}

            while state.pending_steps() and len(state.completed_step_ids) < step_budget:
                step = state.pending_steps()[0]
                step.status = "running"
                state.current_step_id = step.id

                route = await self._route_step(state, step)
                state.next_action = route

                execution = await self._execute_plan_step(task, step, route)
                result.execution_results.append(execution)
                tool_calls += int(execution.get("tool_calls", 0))
                state.observations.append(self._build_execution_observation(step, execution))

                if tool_calls > tool_budget:
                    state.status = "failed"
                    state.errors.append(
                        ErrorRecord(
                            step_id=step.id,
                            category="environment_issue",
                            message=f"Tool call budget exceeded ({tool_budget})",
                        )
                    )
                    step.status = "failed"
                    break

                validation = await self._validate_step(task, state, step, execution)
                if validation.get("passed", False):
                    step.status = "done"
                    state.completed_step_ids.append(step.id)
                    state.observations.append(
                        Observation(
                            source="validator",
                            summary=f"Step '{step.title}' passed validation",
                            raw_ref=json.dumps(validation, ensure_ascii=False),
                        )
                    )
                    for artifact in execution.get("artifacts", []):
                        result.code_artifacts.append(artifact)
                        state.artifacts.append(
                            ArtifactRecord(
                                path=artifact.get("filename"),
                                kind=str(artifact.get("kind") or "file"),
                                description=f"Generated by step '{step.title}'",
                            )
                        )
                    continue

                step.status = "failed"
                category = self._classify_failure(execution.get("answer", ""))
                error_record = ErrorRecord(
                    step_id=step.id,
                    category=category,
                    message="Step validation failed",
                    raw_output=execution.get("answer", ""),
                )
                state.errors.append(error_record)

                reflection = await self._reflect_failure(task, state, step, execution, category)
                state.reflections.append(reflection)

                attempts[step.id] = attempts.get(step.id, 0) + 1
                if reflection.requires_user_input:
                    state.status = "needs_input"
                    break

                if reflection.should_retry and attempts[step.id] <= 1:
                    step.status = "pending"
                    if reflection.selected_fix:
                        step.inputs["retry_hint"] = reflection.selected_fix
                    continue

                if reflection.should_replan and reflection.selected_fix:
                    recovery_step = PlanStep(
                        id=f"step-{uuid.uuid4().hex[:8]}",
                        title=f"Recovery for {step.title}",
                        purpose=reflection.selected_fix,
                        action_type=step.action_type,
                        expected_output=step.expected_output,
                        verification_method=step.verification_method,
                        status="pending",
                    )
                    state.plan.append(recovery_step)

            if state.status not in {"needs_input", "blocked", "failed"}:
                unfinished = [step for step in state.plan if step.status in {"pending", "running", "failed"}]
                state.status = "completed" if not unfinished else "failed"

            if result.code_artifacts:
                result.review_result = await self._review_artifacts(result.code_artifacts)
                result.test_artifacts = await self._generate_tests(result.code_artifacts)

            result.success = state.status == "completed"
            if not result.success and state.errors:
                result.error = state.errors[-1].message

            result.summary = await self._finalize_summary(task, state, result)

        except Exception as exc:
            logger.error("Orchestrator workflow failed", error=str(exc))
            state.status = "failed"
            state.errors.append(
                ErrorRecord(
                    step_id=state.current_step_id,
                    category="unknown",
                    message=str(exc),
                )
            )
            result.success = False
            result.error = str(exc)
            result.summary = str(exc)

        return self._finalize_result(result, state)

    async def run_parallel(self, task: str, max_parallel_steps: int = 3) -> WorkflowResult:
        """Compatibility wrapper.

        The high-agency loop uses adaptive sequencing and retries, so this method
        delegates to :meth:`run` while preserving API compatibility.
        """
        result = await self.run(task)
        if result.summary:
            result.summary += f"\n\nParallel hint received: requested workers={max_parallel_steps}."
        else:
            result.summary = f"Parallel hint received: requested workers={max_parallel_steps}."
        return result

    async def stream_run(self, task: str) -> AsyncIterator[str]:
        """Stream high-level execution progress for CLI/UI consumption."""
        yield f"Starting task: {task}\n\n"
        yield "Stage 1/5: Normalize task\n"
        yield "Stage 2/5: Observe environment\n"
        yield "Stage 3/5: Plan and route\n"
        yield "Stage 4/5: Execute with validation/reflection\n"

        result = await self.run(task)
        yield "Stage 5/5: Finalize\n\n"

        if result.plan:
            yield "Plan:\n"
            for idx, step in enumerate(result.plan, 1):
                yield f"{idx}. {step}\n"

        if result.success:
            yield "\nTask completed successfully.\n"
        else:
            yield f"\nTask failed: {result.error}\n"

        if result.summary:
            yield f"\nSummary:\n{result.summary}\n"

    async def _normalize_task(self, task: str, state: AgentTaskState) -> AgentTaskState:
        prompt = (
            "Normalize this user request into task JSON. "
            "If some fields are unclear, infer practical defaults.\n\n"
            f"Request:\n{task}"
        )
        response = await self._normalizer.run(prompt)
        parsed = self._parse_json_object(response.answer)

        if parsed:
            state.goal = str(parsed.get("goal") or task)
            state.success_criteria = self._normalize_list(parsed.get("success_criteria")) or [
                "Implement requested functionality",
                "Keep existing behavior stable",
                "Provide verifiable output",
            ]
            state.constraints = self._normalize_list(parsed.get("constraints"))
            state.deliverables = self._normalize_list(parsed.get("deliverables"))
            raw_context = parsed.get("context")
            state.context = raw_context if isinstance(raw_context, dict) else {"notes": str(raw_context or "")}

            budget = parsed.get("budget")
            if isinstance(budget, dict):
                state.budget.max_steps = max(1, int(budget.get("max_steps", state.budget.max_steps)))
                state.budget.max_runtime_ms = max(
                    1000,
                    int(budget.get("max_runtime_ms", state.budget.max_runtime_ms)),
                )
                state.budget.max_tool_calls = max(
                    1,
                    int(budget.get("max_tool_calls", state.budget.max_tool_calls)),
                )
        else:
            state.success_criteria = [
                "Deliver the requested change",
                "Avoid regressions in touched modules",
            ]
            state.deliverables = ["Code changes", "Execution summary"]

        state.observations.append(
            Observation(
                source="normalizer",
                summary=f"Task normalized with risk='{state.risk_level}'",
                raw_ref=response.answer[:800],
            )
        )
        return state

    async def _observe_environment(self, state: AgentTaskState) -> None:
        file_manager = self._tools_by_name.get("file_manager")
        if file_manager is None:
            state.observations.append(
                Observation(
                    source="observation",
                    summary="No file_manager tool available for repository observation",
                )
            )
            return

        decision = self._policy_engine.evaluate("file_manager", {"action": "tree", "path": ".", "max_depth": 2})
        if not decision.allowed:
            message = "Policy blocked environment observation with file_manager"
            state.errors.append(
                ErrorRecord(
                    step_id=None,
                    category="permission_issue",
                    message=message,
                    raw_output=decision.reason,
                )
            )
            return

        try:
            tree_result = await file_manager.run(action="tree", path=".", max_depth=2)
        except Exception as exc:
            state.errors.append(
                ErrorRecord(
                    step_id=None,
                    category="environment_issue",
                    message=f"Observation failed: {exc}",
                )
            )
            return

        if tree_result.success:
            sample = "\n".join(tree_result.output.splitlines()[:20])
            state.observations.append(
                Observation(
                    source="file_manager.tree",
                    summary="Captured repository tree snapshot",
                    raw_ref=sample,
                )
            )
        else:
            state.errors.append(
                ErrorRecord(
                    step_id=None,
                    category="path_issue",
                    message=tree_result.error or "Could not inspect workspace",
                )
            )

    async def _build_plan(self, state: AgentTaskState) -> None:
        prompt = (
            "Create an executable plan for this task state.\n\n"
            f"Goal: {state.goal}\n"
            f"Success criteria: {state.success_criteria}\n"
            f"Constraints: {state.constraints}\n"
            f"Observations: {[obs.summary for obs in state.observations[-3:]]}"
        )
        response = await self._planner.run(prompt)
        parsed = self._parse_json_object(response.answer)

        plan_steps: list[PlanStep] = []
        if parsed and isinstance(parsed.get("plan"), list):
            for idx, item in enumerate(parsed["plan"], 1):
                if not isinstance(item, dict):
                    continue
                action_type = str(item.get("action_type") or "code")
                if action_type not in {"read", "search", "exec", "write", "code", "test", "verify", "ask"}:
                    action_type = self._infer_action_type(str(item.get("purpose") or ""))
                plan_steps.append(
                    PlanStep(
                        id=str(item.get("id") or f"step-{idx}"),
                        title=str(item.get("title") or f"Step {idx}"),
                        purpose=str(item.get("purpose") or ""),
                        action_type=action_type,
                        tool_name=str(item.get("tool_name")) if item.get("tool_name") else None,
                        inputs=item.get("inputs") if isinstance(item.get("inputs"), dict) else {},
                        expected_output=str(item.get("expected_output") or ""),
                        verification_method=str(item.get("verification_method") or ""),
                    )
                )

        if not plan_steps:
            fallback_steps = self._extract_plan_steps(response.answer)
            for idx, step_text in enumerate(fallback_steps, 1):
                plan_steps.append(
                    PlanStep(
                        id=f"step-{idx}",
                        title=f"Step {idx}",
                        purpose=step_text,
                        action_type=self._infer_action_type(step_text),
                        expected_output="Step output produced",
                        verification_method="Review tool output and artifacts",
                    )
                )

        if not plan_steps:
            plan_steps.append(
                PlanStep(
                    id="step-1",
                    title="Implement request",
                    purpose=state.goal,
                    action_type="code",
                    expected_output="Requested change completed",
                    verification_method="Validate behavior and summarize changes",
                )
            )

        state.plan = plan_steps
        state.observations.append(
            Observation(
                source="planner",
                summary=f"Generated {len(plan_steps)} plan step(s)",
                raw_ref=response.answer[:1200],
            )
        )

    async def _route_step(self, state: AgentTaskState, step: PlanStep) -> NextAction:
        default_action = self._tool_router.route(step)

        prompt = (
            "Route this plan step to the best next action.\n\n"
            f"Step title: {step.title}\n"
            f"Step purpose: {step.purpose}\n"
            f"Suggested action_type: {step.action_type}\n"
            f"Known tools: {list(self._tools_by_name)}"
        )
        response = await self._router.run(prompt)
        parsed = self._parse_json_object(response.answer)
        if not parsed:
            return default_action

        route_type = str(parsed.get("type") or step.action_type)
        if route_type not in {"read", "search", "exec", "write", "code", "test", "verify", "ask"}:
            route_type = default_action.type

        tool_name = parsed.get("tool_name")
        if tool_name and tool_name not in self._tools_by_name:
            tool_name = default_action.tool_name

        route_input = parsed.get("input")
        return NextAction(
            type=route_type,
            tool_name=str(tool_name or default_action.tool_name) if (tool_name or default_action.tool_name) else None,
            reason=str(parsed.get("reason") or default_action.reason),
            input=route_input if isinstance(route_input, dict) else default_action.input,
        )

    async def _execute_plan_step(
        self,
        task: str,
        step: PlanStep,
        route: NextAction,
    ) -> dict[str, Any]:
        prompt = (
            "Execute only this plan step. Prefer execution over explanation.\n\n"
            f"Task goal: {task}\n"
            f"Step title: {step.title}\n"
            f"Step purpose: {step.purpose}\n"
            f"Action type: {route.type}\n"
            f"Preferred tool: {route.tool_name or 'auto'}\n"
            f"Route reason: {route.reason}\n"
            f"Inputs: {route.input}\n"
            f"Expected output: {step.expected_output}\n"
            f"Verification method: {step.verification_method}\n"
        )
        if step.inputs.get("retry_hint"):
            prompt += f"Retry hint: {step.inputs['retry_hint']}\n"

        response = await self._coder.run(prompt)
        return {
            "step_id": step.id,
            "action_type": route.type,
            "success": response.success,
            "answer": response.answer,
            "artifacts": response.code_artifacts,
            "tool_events": [
                item.model_dump(mode="json")
                for item in response.steps
                if item.action != "final_answer"
            ],
            "evidence": self._extract_execution_evidence(response.steps, response.answer),
            "tool_calls": len([s for s in response.steps if s.action != "final_answer"]),
        }

    async def _validate_step(
        self,
        task: str,
        state: AgentTaskState,
        step: PlanStep,
        execution: dict[str, Any],
    ) -> dict[str, Any]:
        answer = str(execution.get("answer") or "").strip()
        artifacts = execution.get("artifacts", [])
        tool_events = execution.get("tool_events", [])
        evidence = execution.get("evidence", [])
        deterministic = self._deterministic_validation(step, execution)
        deterministic_pass = bool(deterministic.get("passed", True))

        heuristic_pass = bool(answer) and execution.get("success", False)
        if step.action_type in {"write", "code", "test"} and not artifacts:
            heuristic_pass = False
        if tool_events and not any(bool(event.get("tool_success")) for event in tool_events) and not artifacts:
            heuristic_pass = False
        if "could not complete" in answer.lower():
            heuristic_pass = False
        if not deterministic_pass:
            heuristic_pass = False

        prompt = (
            "Validate if this step execution is sufficient.\n\n"
            f"Goal: {task}\n"
            f"Step: {step.title} - {step.purpose}\n"
            f"Expected output: {step.expected_output}\n"
            f"Verification method: {step.verification_method}\n"
            f"Execution answer: {answer[:1500]}\n"
            f"Artifact count: {len(artifacts)}\n"
            f"Tool events: {tool_events[:3]}\n"
            f"Evidence: {evidence[:5]}\n"
            f"Deterministic checks: {deterministic}\n"
            f"Recent observations: {[obs.summary for obs in state.observations[-3:]]}"
        )
        response = await self._validator.run(prompt)
        parsed = self._parse_json_object(response.answer) or {}
        llm_pass = bool(parsed.get("passed", heuristic_pass))

        return {
            "passed": bool(deterministic_pass and heuristic_pass and llm_pass),
            "heuristic_pass": heuristic_pass,
            "deterministic": deterministic,
            "validator": parsed,
        }

    async def _reflect_failure(
        self,
        task: str,
        state: AgentTaskState,
        step: PlanStep,
        execution: dict[str, Any],
        category: str,
    ) -> ReflectionRecord:
        prompt = (
            "Reflect on this failed step and provide retry/replan guidance.\n\n"
            f"Goal: {task}\n"
            f"Step: {step.title}\n"
            f"Failure category: {category}\n"
            f"Execution answer: {str(execution.get('answer') or '')[:1500]}\n"
            f"Recent errors: {[err.message for err in state.errors[-3:]]}"
        )
        response = await self._reflection.run(prompt)
        parsed = self._parse_json_object(response.answer)
        if not parsed:
            return fallback_reflection(
                step.id,
                category,
                "Execution output did not satisfy validation.",
            )

        fixes = self._normalize_list(parsed.get("proposed_fixes"))
        if not fixes:
            return fallback_reflection(
                step.id,
                category,
                str(parsed.get("diagnosis") or "Execution output did not satisfy validation."),
            )

        selected_fix = parsed.get("selected_fix")
        if not selected_fix:
            selected_fix = fixes[0]

        should_retry = bool(parsed.get("should_retry", category in {"wrong_parameters", "wrong_tool", "path_issue"}))
        should_replan = bool(parsed.get("should_replan", category in {"task_interpretation_issue", "insufficient_information"}))
        requires_user_input = bool(parsed.get("requires_user_input", False))

        return ReflectionRecord(
            step_id=step.id,
            failure_category=category,
            diagnosis=str(parsed.get("diagnosis") or "Execution output did not satisfy validation."),
            proposed_fixes=fixes,
            selected_fix=str(selected_fix),
            should_retry=should_retry,
            should_replan=should_replan,
            requires_user_input=requires_user_input,
        )

    async def _review_artifacts(self, artifacts: list[dict[str, str]]) -> dict[str, Any]:
        artifacts_text = "\n\n".join(
            f"{a.get('filename', 'output.py')}\n```python\n{a.get('content', '')}\n```"
            for a in artifacts
        )
        review_response = await self._reviewer.run(f"Review the following code:\n\n{artifacts_text}")
        return self._parse_review(review_response.answer)

    async def _generate_tests(self, artifacts: list[dict[str, str]]) -> list[dict[str, str]]:
        test_prompt = (
            "Write pytest tests for the following code:\n\n"
            + "\n\n".join(
                f"{a.get('filename', 'output.py')}\n```python\n{a.get('content', '')}\n```"
                for a in artifacts
            )
        )
        test_response = await self._tester.run(test_prompt)
        return test_response.code_artifacts

    async def _finalize_summary(
        self,
        task: str,
        state: AgentTaskState,
        result: WorkflowResult,
    ) -> str:
        prompt = (
            "Produce a concise final summary with evidence.\n\n"
            f"Task: {task}\n"
            f"Status: {state.status}\n"
            f"Completed steps: {state.completed_step_ids}\n"
            f"Artifacts: {[a.get('filename', '') for a in result.code_artifacts]}\n"
            f"Errors: {[e.message for e in state.errors]}"
        )
        response = await self._finalizer.run(prompt)
        summary = response.answer.strip()
        if summary:
            return summary

        if state.status == "completed":
            return "Task completed with validated execution steps and recorded artifacts."
        if state.errors:
            return f"Task ended with failure: {state.errors[-1].message}"
        return "Task finished without additional summary details."

    def _finalize_result(self, result: WorkflowResult, state: AgentTaskState) -> WorkflowResult:
        result.plan = [step.title for step in state.plan]
        result.observations = [obs.model_dump(mode="json") for obs in state.observations]
        result.reflections = [ref.model_dump(mode="json") for ref in state.reflections]
        result.errors = [err.model_dump(mode="json") for err in state.errors]
        result.task_state = state.model_dump(mode="json")
        if not result.error and state.errors:
            result.error = state.errors[-1].message
        return result

    @staticmethod
    def _build_execution_observation(step: PlanStep, execution: dict[str, Any]) -> Observation:
        tool_events = execution.get("tool_events", [])
        tool_names = [str(event.get("action")) for event in tool_events if str(event.get("action") or "").strip()]
        joined_tools = ", ".join(tool_names) if tool_names else "no direct tool calls"
        raw_ref = json.dumps(
            {
                "answer": str(execution.get("answer") or "")[:500],
                "evidence": execution.get("evidence", [])[:5],
            },
            ensure_ascii=False,
        )
        return Observation(
            source="executor",
            summary=f"Executed step '{step.title}' using {joined_tools}",
            raw_ref=raw_ref[:1200],
        )

    @staticmethod
    def _extract_expected_keywords(expected_output: str) -> list[str]:
        text = (expected_output or "").strip().lower()
        if not text:
            return []

        quoted = [
            item[0] or item[1]
            for item in re.findall(r"'([^']{2,50})'|\"([^\"]{2,50})\"", text)
        ]
        if quoted:
            return [item.strip() for item in quoted if item.strip()][:6]

        stop_words = {
            "the",
            "and",
            "with",
            "that",
            "this",
            "step",
            "output",
            "should",
            "generated",
            "generate",
            "create",
            "created",
            "file",
            "exists",
            "requested",
            "completed",
            "complete",
        }
        tokens = [
            token
            for token in re.findall(r"[a-z0-9_\-]{4,}", text)
            if token not in stop_words
        ]
        return tokens[:6]

    @classmethod
    def _deterministic_validation(cls, step: PlanStep, execution: dict[str, Any]) -> dict[str, Any]:
        tool_events = execution.get("tool_events", [])
        checks: list[str] = []
        hard_failures: list[str] = []

        for event in tool_events:
            action = str(event.get("action") or "")
            metadata = event.get("tool_metadata") or {}
            observation = str(event.get("observation") or "")
            tool_success = event.get("tool_success")

            if tool_success is False:
                hard_failures.append(f"Tool '{action}' reported failure")

            if metadata.get("exit_code") is not None:
                try:
                    exit_code = int(metadata.get("exit_code"))
                except (TypeError, ValueError):
                    exit_code = -1
                if exit_code == 0:
                    checks.append(f"Tool '{action}' exited with code 0")
                else:
                    hard_failures.append(f"Tool '{action}' exited with code {exit_code}")

            path_value = metadata.get("path")
            if path_value:
                path_str = str(path_value)
                exists = Path(path_str).exists()
                if exists:
                    checks.append(f"Path exists: {path_str}")
                elif action in {"file_manager", "script_runner"} or step.action_type in {"write", "code", "test", "verify"}:
                    hard_failures.append(f"Expected path not found: {path_str}")

            if observation and len(observation) > 0:
                checks.append(f"Tool '{action}' produced observation output")

        keywords = cls._extract_expected_keywords(step.expected_output)
        if keywords:
            corpus_parts = [str(execution.get("answer") or "")]
            corpus_parts.extend(str(event.get("observation") or "") for event in tool_events)
            corpus_parts.extend(str(item) for item in execution.get("evidence", []))
            corpus = "\n".join(corpus_parts).lower()
            matched = [keyword for keyword in keywords if keyword in corpus]
            if matched:
                checks.append(f"Expected output keywords matched: {matched}")
            else:
                hard_failures.append(
                    f"No expected output keywords matched in execution output: {keywords}"
                )

        return {
            "passed": not hard_failures,
            "checks": checks[:8],
            "hard_failures": hard_failures[:8],
            "keywords": keywords,
        }

    @staticmethod
    def _extract_execution_evidence(steps: list[Any], answer: str) -> list[str]:
        evidence: list[str] = []
        for step in steps:
            if getattr(step, "action", "") == "final_answer":
                continue
            action = str(getattr(step, "action", "") or "")
            observation = str(getattr(step, "observation", "") or "").strip()
            metadata = getattr(step, "tool_metadata", {}) or {}
            success = getattr(step, "tool_success", None)

            bits = [f"tool={action}"]
            if success is not None:
                bits.append(f"success={success}")
            if metadata.get("path"):
                bits.append(f"path={metadata['path']}")
            if metadata.get("exit_code") is not None:
                bits.append(f"exit_code={metadata['exit_code']}")
            if observation:
                bits.append(f"output={observation[:200]}")
            evidence.append("; ".join(bits))

        if answer.strip():
            evidence.append(f"answer={answer.strip()[:200]}")
        return evidence[:6]

    @staticmethod
    def _normalize_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any] | None:
        fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fenced_match:
            candidate = fenced_match.group(1)
        else:
            bare_match = re.search(r"\{.*\}", text, re.DOTALL)
            candidate = bare_match.group(0) if bare_match else ""

        if not candidate:
            return None

        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            return None

        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _infer_action_type(step_text: str) -> str:
        return ToolRouter.infer_action_type(step_text)

    @staticmethod
    def _classify_risk(task: str) -> str:
        normalized = task.lower()
        if any(keyword in normalized for keyword in _HIGH_RISK_KEYWORDS):
            return "high"
        if any(keyword in normalized for keyword in {"migrate", "refactor", "database", "production"}):
            return "medium"
        return "low"

    @staticmethod
    def _classify_failure(output: str) -> str:
        return classify_failure_output(output)

    @staticmethod
    def _extract_plan_steps(plan_text: str) -> list[str]:
        steps = re.findall(r"^\s*\d+[\.\)]\s+(.+)$", plan_text, re.MULTILINE)
        if steps:
            return steps
        stripped = plan_text.strip()
        return [stripped] if stripped else []

    @staticmethod
    def _parse_review(review_text: str) -> dict[str, Any]:
        fenced_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", review_text, re.DOTALL)
        if fenced_match:
            json_str = fenced_match.group(1)
        else:
            bare_match = re.search(r"\{.*\}", review_text, re.DOTALL)
            json_str = bare_match.group(0) if bare_match else None

        if json_str:
            try:
                parsed = json.loads(json_str)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass

        return {"passed": True, "score": 7, "issues": [], "raw": review_text}


