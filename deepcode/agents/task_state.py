"""Structured state models for high-agency task execution."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


ActionType = Literal["read", "search", "exec", "write", "code", "test", "verify", "ask"]
PlanStepStatus = Literal["pending", "running", "done", "failed", "skipped"]
FailureCategory = Literal[
    "insufficient_information",
    "wrong_tool",
    "wrong_parameters",
    "path_issue",
    "permission_issue",
    "missing_dependency",
    "environment_issue",
    "network_issue",
    "logic_bug",
    "task_interpretation_issue",
    "unknown",
]
TaskRiskLevel = Literal["low", "medium", "high"]
TaskStatus = Literal["idle", "running", "blocked", "needs_input", "failed", "completed"]


class TaskBudget(BaseModel):
    """Execution budget boundaries for a task."""

    max_steps: int = 8
    max_runtime_ms: int = 180000
    max_tool_calls: int = 24


class PlanStep(BaseModel):
    """Single executable plan step."""

    id: str
    title: str
    purpose: str
    action_type: ActionType = "code"
    tool_name: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected_output: str = ""
    verification_method: str = ""
    status: PlanStepStatus = "pending"


class Observation(BaseModel):
    """Observed evidence captured during execution."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str
    summary: str
    raw_ref: str | None = None


class ArtifactRecord(BaseModel):
    """Artifact produced during a task run."""

    path: str | None = None
    kind: Literal["file", "script", "report", "json", "log", "other"] = "other"
    description: str


class ErrorRecord(BaseModel):
    """Error details captured for failed actions."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    step_id: str | None = None
    category: FailureCategory = "unknown"
    message: str
    raw_output: str | None = None


class ReflectionRecord(BaseModel):
    """Structured reflection output for retries/replanning."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    step_id: str | None = None
    failure_category: FailureCategory = "unknown"
    diagnosis: str = ""
    proposed_fixes: list[str] = Field(default_factory=list)
    selected_fix: str | None = None
    should_retry: bool = False
    should_replan: bool = False
    requires_user_input: bool = False


class NextAction(BaseModel):
    """Router output describing next action to execute."""

    type: ActionType
    reason: str
    tool_name: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)


class AgentTaskState(BaseModel):
    """Durable state for one high-agency task execution loop."""

    goal: str
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    deliverables: list[str] = Field(default_factory=list)
    risk_level: TaskRiskLevel = "low"
    budget: TaskBudget = Field(default_factory=TaskBudget)
    plan: list[PlanStep] = Field(default_factory=list)
    current_step_id: str | None = None
    completed_step_ids: list[str] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    errors: list[ErrorRecord] = Field(default_factory=list)
    reflections: list[ReflectionRecord] = Field(default_factory=list)
    next_action: NextAction | None = None
    status: TaskStatus = "idle"

    def pending_steps(self) -> list[PlanStep]:
        """Return pending plan steps in insertion order."""
        return [step for step in self.plan if step.status == "pending"]

    def get_step(self, step_id: str) -> PlanStep | None:
        """Return a step by id if present."""
        for step in self.plan:
            if step.id == step_id:
                return step
        return None
