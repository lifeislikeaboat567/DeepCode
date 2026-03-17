"""Task management routes for long-running orchestrated workflows."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import StreamingResponse

from deepcode.agents.orchestrator import OrchestratorAgent
from deepcode.api.models import CodeArtifact, CreateTaskRequest, TaskStatusResponse
from deepcode.llm.factory import create_llm_client
from deepcode.logging_config import get_logger
from deepcode.tools import CodeExecutorTool, FileManagerTool, ShellTool

router = APIRouter()
logger = get_logger(__name__)

# In-memory task registry (sufficient for single-process deployment)
_tasks: dict[str, dict[str, Any]] = {}


def _build_orchestrator() -> OrchestratorAgent:
    """Create a fresh orchestrator with the configured LLM and standard tools."""
    llm = create_llm_client()
    tools = [CodeExecutorTool(), FileManagerTool(), ShellTool()]
    return OrchestratorAgent(llm=llm, tools=tools)


async def _run_task(task_id: str, task_text: str) -> None:
    """Background coroutine that runs the orchestrator and updates the registry."""
    _tasks[task_id]["status"] = "running"
    try:
        orchestrator = _build_orchestrator()
        result = await orchestrator.run(task_text)
        _tasks[task_id].update(
            {
                "status": "completed" if result.success else "failed",
                "plan": result.plan,
                "code_artifacts": result.code_artifacts,
                "review_result": result.review_result,
                "summary": result.summary,
                "error": result.error,
            }
        )
    except Exception as exc:
        logger.error("Task failed", task_id=task_id, error=str(exc))
        _tasks[task_id].update({"status": "failed", "error": str(exc)})


@router.post("", response_model=TaskStatusResponse, status_code=status.HTTP_202_ACCEPTED, tags=["Tasks"])
async def create_task(request: CreateTaskRequest, background_tasks: BackgroundTasks) -> TaskStatusResponse:
    """Create and enqueue a new orchestrated task.

    The task runs asynchronously; poll ``GET /api/v1/tasks/{id}`` for status.
    """
    task_id = str(uuid.uuid4())
    _tasks[task_id] = {
        "task_id": task_id,
        "task": request.task,
        "status": "pending",
        "plan": [],
        "code_artifacts": [],
        "review_result": {},
        "summary": "",
        "error": "",
    }

    background_tasks.add_task(_run_task, task_id, request.task)
    logger.info("Task created", task_id=task_id)

    return TaskStatusResponse(
        task_id=task_id,
        task=request.task,
        status="pending",
    )


@router.get("/{task_id}", response_model=TaskStatusResponse, tags=["Tasks"])
async def get_task(task_id: str) -> TaskStatusResponse:
    """Get the current status and results of a task."""
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found",
        )

    return TaskStatusResponse(
        task_id=task["task_id"],
        task=task["task"],
        status=task["status"],
        plan=task.get("plan", []),
        code_artifacts=[
            CodeArtifact(
                filename=a.get("filename", "output.py"),
                content=a.get("content", ""),
            )
            for a in task.get("code_artifacts", [])
        ],
        review_result=task.get("review_result", {}),
        summary=task.get("summary", ""),
        error=task.get("error", ""),
    )


@router.get("/{task_id}/stream", tags=["Tasks"])
async def stream_task(task_id: str) -> StreamingResponse:
    """Stream progress updates for a task as Server-Sent Events."""

    async def event_generator():
        """Poll the task registry and yield status updates."""
        last_status = None
        max_polls = 300  # 5 minutes at 1s interval

        for _ in range(max_polls):
            task = _tasks.get(task_id)
            if task is None:
                yield f"data: Task {task_id} not found\n\n"
                return

            current_status = task["status"]
            if current_status != last_status:
                yield f"data: status={current_status}\n\n"
                last_status = current_status

            if current_status in ("completed", "failed"):
                summary = task.get("summary") or task.get("error", "")
                yield f"data: {summary}\n\n"
                yield "data: [DONE]\n\n"
                return

            await asyncio.sleep(1)

        yield "data: Timeout waiting for task completion\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
