"""Pydantic request/response models for the DeepCode REST API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# ─── Chat ─────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    """Request body for ``POST /api/v1/chat``."""

    message: str = Field(min_length=1, description="User message")
    session_id: str | None = Field(default=None, description="Optional existing session ID")
    stream: bool = Field(default=False, description="Whether to use streaming response")


class CodeArtifact(BaseModel):
    """A code file produced during an agent run."""

    filename: str
    content: str
    language: str = "python"


class AgentStep(BaseModel):
    """A single reasoning step in the agent's response."""

    thought: str = ""
    action: str = ""
    observation: str = ""


class ChatResponse(BaseModel):
    """Response body for ``POST /api/v1/chat``."""

    session_id: str
    message: str
    code_artifacts: list[CodeArtifact] = Field(default_factory=list)
    steps: list[AgentStep] = Field(default_factory=list)
    success: bool = True
    error: str = ""


# ─── Sessions ─────────────────────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    """Request body for ``POST /api/v1/sessions``."""

    name: str = Field(default="New Session", min_length=1)


class MessageSchema(BaseModel):
    """A message in the session history."""

    role: str
    content: str
    created_at: datetime


class SessionSchema(BaseModel):
    """Session representation returned by the API."""

    id: str
    name: str
    message_count: int
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionDetailSchema(SessionSchema):
    """Extended session representation including full message history."""

    messages: list[MessageSchema] = Field(default_factory=list)


# ─── Tasks ────────────────────────────────────────────────────────────────────

class CreateTaskRequest(BaseModel):
    """Request body for ``POST /api/v1/tasks``."""

    task: str = Field(min_length=1, description="Natural language task description")
    session_id: str | None = Field(default=None)


class TaskStatusResponse(BaseModel):
    """Response describing a task's current status."""

    task_id: str
    task: str
    status: str  # pending | running | completed | failed
    plan: list[str] = Field(default_factory=list)
    code_artifacts: list[CodeArtifact] = Field(default_factory=list)
    review_result: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    error: str = ""


# ─── Health ───────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    """Response for ``GET /api/v1/health``."""

    status: str = "ok"
    version: str
    llm_provider: str
    llm_model: str
