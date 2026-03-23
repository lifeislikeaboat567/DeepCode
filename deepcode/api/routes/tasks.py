"""Task management routes for long-running orchestrated workflows."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import StreamingResponse

from deepcode.agents.orchestrator import OrchestratorAgent
from deepcode.api.models import CodeArtifact, CreateTaskRequest, TaskStatusResponse
from deepcode.exceptions import TaskNotFoundError
from deepcode.llm.factory import create_llm_client
from deepcode.logging_config import get_logger
from deepcode.storage import TaskRecord, TaskStore
from deepcode.tools import build_default_tools

router = APIRouter()
logger = get_logger(__name__)
_store = TaskStore()


def _build_orchestrator() -> OrchestratorAgent:
    """Create a fresh orchestrator with the configured LLM and standard tools."""
    llm = create_llm_client()
    tools = build_default_tools()
    return OrchestratorAgent(llm=llm, tools=tools)


def _to_response(record: TaskRecord) -> TaskStatusResponse:
    """Convert :class:`TaskRecord` to API schema."""
    return TaskStatusResponse(
        task_id=record.id,
        task=record.task,
        session_id=record.session_id,
        status=record.status,
        plan=record.plan,
        code_artifacts=[
            CodeArtifact(
                filename=a.get("filename", "output.py"),
                content=a.get("content", ""),
                language=a.get("language", "python"),
            )
            for a in record.code_artifacts
        ],
        review_result=record.review_result,
        execution_results=record.execution_results,
        task_state=record.task_state,
        observations=record.observations,
        reflections=record.reflections,
        errors=record.errors,
        summary=record.summary,
        error=record.error,
        created_at=record.created_at,
        updated_at=record.updated_at,
        metadata=record.metadata,
    )


async def _run_task(task_id: str, task_text: str) -> None:
    """Background coroutine that runs the orchestrator and updates the registry."""
    await _store.set_status(task_id, "running")
    try:
        orchestrator = _build_orchestrator()
        result = await orchestrator.run(task_text)
        await _store.set_status(
            task_id,
            "completed" if result.success else "failed",
            plan=result.plan,
            code_artifacts=result.code_artifacts,
            review_result=result.review_result,
            execution_results=result.execution_results,
            task_state=result.task_state,
            observations=result.observations,
            reflections=result.reflections,
            errors=result.errors,
            summary=result.summary,
            error=result.error,
        )
    except Exception as exc:
        logger.error("Task failed", task_id=task_id, error=str(exc))
        await _store.set_status(task_id, "failed", error=str(exc))


@router.post("", response_model=TaskStatusResponse, status_code=status.HTTP_202_ACCEPTED, tags=["Tasks"])
async def create_task(request: CreateTaskRequest, background_tasks: BackgroundTasks) -> TaskStatusResponse:
    """Create and enqueue a new orchestrated task.

    The task runs asynchronously; poll ``GET /api/v1/tasks/{id}`` for status.
    """
    record = await _store.create(
        task=request.task,
        session_id=request.session_id,
        metadata={"origin": "api"},
    )
    background_tasks.add_task(_run_task, record.id, request.task)
    logger.info("Task created", task_id=record.id)
    return _to_response(record)


@router.get("", response_model=list[TaskStatusResponse], tags=["Tasks"])
async def list_tasks(limit: int = 50) -> list[TaskStatusResponse]:
    """List recent tasks for task center views."""
    records = await _store.list_all(limit=limit)
    return [_to_response(record) for record in records]


@router.get("/{task_id}", response_model=TaskStatusResponse, tags=["Tasks"])
async def get_task(task_id: str) -> TaskStatusResponse:
    """Get the current status and results of a task."""
    try:
        task = await _store.get(task_id)
    except TaskNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found",
        )
    return _to_response(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Tasks"])
async def delete_task(task_id: str) -> None:
    """Delete a task and its artifacts from storage."""
    try:
        await _store.delete(task_id)
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{task_id}/stream", tags=["Tasks"])
async def stream_task(task_id: str) -> StreamingResponse:
    """Stream progress updates for a task as Server-Sent Events."""

    async def event_generator():
        """Poll the task registry and yield status updates."""
        last_status = None
        max_polls = 300  # 5 minutes at 1s interval

        for _ in range(max_polls):
            try:
                task = await _store.get(task_id)
            except TaskNotFoundError:
                yield f"data: Task {task_id} not found\n\n"
                return

            current_status = task.status
            if current_status != last_status:
                yield f"data: status={current_status}\n\n"
                last_status = current_status

            if current_status in ("completed", "failed"):
                summary = task.summary or task.error
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
