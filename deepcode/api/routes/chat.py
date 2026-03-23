"""Chat routes for single-turn and streaming agent interactions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from deepcode.api.models import AgentStep as ApiAgentStep, ChatRequest, ChatResponse
from deepcode.chat_runtime import (
    complete_agent_response,
    complete_chat_response,
    normalize_chat_mode,
    stream_agent_events,
    stream_chat_response,
)
from deepcode.llm.factory import create_llm_client
from deepcode.logging_config import get_logger
from deepcode.storage import Message, SessionStore, TaskStore
from deepcode.tools import build_default_tools

router = APIRouter()
logger = get_logger(__name__)
_store = SessionStore()
_task_store = TaskStore()


def _append_agent_run_metadata(
    session,
    *,
    assistant_message_id: str,
    user_message: str,
    assistant_message: str,
    agent_context: dict[str, Any],
    plan_only: bool,
) -> None:
    intent_route = agent_context.get("intent_route") if isinstance(agent_context, dict) else {}
    decomposed = agent_context.get("decomposed_task") if isinstance(agent_context, dict) else {}
    skills = agent_context.get("relevant_skills") if isinstance(agent_context, dict) else []
    mcp_servers = agent_context.get("relevant_mcp_servers") if isinstance(agent_context, dict) else []
    intent = str((intent_route or {}).get("intent", "")).strip()
    rationale = str((intent_route or {}).get("rationale", "")).strip()
    preferred_tools = (intent_route or {}).get("preferred_tools")
    subtasks = (decomposed or {}).get("subtasks")
    if not isinstance(subtasks, list):
        subtasks = []
    if not subtasks and isinstance((intent_route or {}).get("subtasks"), list):
        subtasks = list((intent_route or {}).get("subtasks"))
    plan_rows = [str(item).strip() for item in subtasks if str(item).strip()]
    skills_rows = [
        f"- {str(item.get('name', '')).strip()}: {str(item.get('description', '')).strip()}".rstrip(": ")
        for item in (skills if isinstance(skills, list) else [])
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    ]
    mcp_rows = [
        (
            f"- {str(item.get('name', '')).strip()}"
            + (
                f" ({str(item.get('transport', '')).strip()})"
                if str(item.get("transport", "")).strip()
                else ""
            )
        )
        for item in (mcp_servers if isinstance(mcp_servers, list) else [])
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    ]
    intent_lines: list[str] = []
    if intent:
        intent_lines.append(f"Intent: {intent}")
    if rationale:
        intent_lines.append(f"Rationale: {rationale}")
    if isinstance(preferred_tools, list):
        preferred = ", ".join(str(item).strip() for item in preferred_tools if str(item).strip())
        if preferred:
            intent_lines.append(f"Preferred tools: {preferred}")

    metadata = dict(session.metadata or {})
    runs = metadata.get("agent_runs")
    if not isinstance(runs, list):
        runs = []
    runs.append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "assistant_message_id": assistant_message_id,
            "user_message": user_message,
            "assistant_message": assistant_message,
            "plan_only": bool(plan_only),
            "agent_context": agent_context,
            "trace_reason": "",
            "trace_function_call": "",
            "trace_observation": "",
            "trace_elapsed": "",
            "trace_collapsed": "1",
            "trace_intent": "\n".join(intent_lines).strip(),
            "trace_plan": "\n".join(f"{idx}. {item}" for idx, item in enumerate(plan_rows, 1)).strip(),
            "trace_skills": "\n".join(skills_rows).strip(),
            "trace_mcp": "\n".join(mcp_rows).strip(),
        }
    )
    metadata["agent_runs"] = runs[-50:]
    session.metadata = metadata


async def _persist_agent_task_snapshot(
    *,
    session_id: str,
    user_message: str,
    assistant_message: str,
    plan_only: bool,
    agent_context: dict[str, Any],
) -> None:
    decomposed = agent_context.get("decomposed_task") if isinstance(agent_context, dict) else {}
    intent = agent_context.get("intent_route") if isinstance(agent_context, dict) else {}
    goal = str((decomposed or {}).get("goal") or user_message).strip() or user_message
    subtasks = (decomposed or {}).get("subtasks")
    if not isinstance(subtasks, list):
        subtasks = []
    plan = [str(item).strip() for item in subtasks if str(item).strip()]

    task = await _task_store.create(
        task=goal,
        session_id=session_id,
        metadata={
            "origin": "chat_agent",
            "plan_only": bool(plan_only),
            "intent": str((intent or {}).get("intent", "")),
        },
    )
    await _task_store.set_status(
        task.id,
        "completed",
        plan=plan,
        summary=assistant_message,
        task_state={"chat_agent_context": agent_context},
        observations=[
            {
                "source": "chat_agent",
                "summary": f"Chat agent {'planned' if plan_only else 'executed'} request",
                "raw_ref": user_message[:1000],
            }
        ],
    )


@router.post("", response_model=ChatResponse, tags=["Chat"])
async def chat(request: ChatRequest) -> ChatResponse:
    """Send a message to the agent and receive a complete response.

    If *session_id* is provided, the conversation history is loaded from the
    store and the new exchange is appended.
    """
    # Resolve or create session
    if request.session_id:
        try:
            session = await _store.get(request.session_id)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {request.session_id}",
            ) from exc
    else:
        session = await _store.create()

    session.messages.append(Message(role="user", content=request.message))
    await _store.update(session)

    mode = normalize_chat_mode(request.mode)
    llm = create_llm_client()
    ask_tools = build_default_tools()
    steps: list[ApiAgentStep] = []
    code_artifacts: list[dict[str, str]] = []
    agent_context: dict[str, Any] = {}
    try:
        if mode == "agent":
            agent_result = await complete_agent_response(
                llm,
                session.messages,
                tools=build_default_tools(),
                plan_only=bool(request.plan_only),
            )
            answer = str(agent_result.answer or "")
            agent_context = dict(agent_result.agent_context or {})
            steps = [
                ApiAgentStep(
                    thought=step.thought,
                    action=step.action,
                    action_input=dict(step.action_input),
                    observation=step.observation,
                    tool_success=step.tool_success,
                )
                for step in agent_result.steps
            ]
            code_artifacts = [
                {
                    "filename": str(artifact.get("filename", "artifact.txt")),
                    "content": str(artifact.get("content", "")),
                    "language": str(artifact.get("language", "text")),
                }
                for artifact in agent_result.code_artifacts
            ]
        else:
            answer = await complete_chat_response(llm, session.messages, ask_tools)
    except Exception as exc:
        logger.error("Chat completion failed", error=str(exc), session_id=session.id, mode=mode)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Chat error: {exc}",
        ) from exc

    session.messages.append(Message(role="assistant", content=answer))
    assistant_message_id = f"assistant-{session.messages[-1].created_at.isoformat()}"
    if mode == "agent":
        _append_agent_run_metadata(
            session,
            assistant_message_id=assistant_message_id,
            user_message=request.message,
            assistant_message=answer,
            agent_context=agent_context,
            plan_only=bool(request.plan_only),
        )
    await _store.update(session)
    if mode == "agent":
        try:
            await _persist_agent_task_snapshot(
                session_id=session.id,
                user_message=request.message,
                assistant_message=answer,
                plan_only=bool(request.plan_only),
                agent_context=agent_context,
            )
        except Exception as exc:
            logger.warning("Failed to persist chat agent task snapshot", error=str(exc), session_id=session.id)

    return ChatResponse(
        session_id=session.id,
        mode=mode,
        message=answer,
        code_artifacts=code_artifacts,
        steps=steps,
        agent_context=agent_context,
        success=True,
        error="",
    )


@router.get("/stream", tags=["Chat"])
async def chat_stream(
    message: str,
    session_id: str | None = None,
    mode: str = "ask",
    plan_only: bool = False,
) -> StreamingResponse:
    """Stream an agent response as Server-Sent Events (SSE).

    Args:
        message: The user's message.
        session_id: Optional existing session ID.
    """

    if session_id:
        try:
            session = await _store.get(session_id)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            ) from exc
    else:
        session = await _store.create()

    session.messages.append(Message(role="user", content=message))
    await _store.update(session)
    resolved_mode = normalize_chat_mode(mode)

    async def event_generator():
        llm = create_llm_client()
        full_answer = ""
        final_answer_only = ""
        agent_context: dict[str, Any] = {}
        yield (
            "data: "
            + json.dumps(
                {
                    "type": "start",
                    "payload": {
                        "session_id": session.id,
                        "mode": resolved_mode,
                        "plan_only": bool(plan_only),
                    },
                },
                ensure_ascii=False,
            )
            + "\n\n"
        )
        try:
            ask_tools = build_default_tools()
            if resolved_mode == "agent":
                async for event in stream_agent_events(
                    llm,
                    session.messages,
                    tools=build_default_tools(),
                    plan_only=bool(plan_only),
                ):
                    event_type = str(event.get("type", ""))
                    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                    if event_type == "agent_context":
                        agent_context = dict(payload)
                    if event_type == "final_answer":
                        final_answer_only = str(payload.get("answer", "")).strip()
                        full_answer = final_answer_only or full_answer
                    yield f"data: {json.dumps({'type': event_type, 'payload': payload}, ensure_ascii=False)}\n\n"
            else:
                async for chunk in stream_chat_response(llm, session.messages, ask_tools):
                    full_answer += chunk
                    yield f"data: {json.dumps({'type': 'chunk', 'payload': {'content': chunk}}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.error("Chat stream failed", error=str(exc), session_id=session.id, mode=resolved_mode)
            yield f"data: {json.dumps({'type': 'error', 'payload': {'message': str(exc)}}, ensure_ascii=False)}\n\n"
        else:
            persisted_answer = final_answer_only.strip() if resolved_mode == "agent" else full_answer
            if not persisted_answer:
                persisted_answer = full_answer
            session.messages.append(Message(role="assistant", content=persisted_answer))
            assistant_message_id = f"assistant-{session.messages[-1].created_at.isoformat()}"
            if resolved_mode == "agent":
                _append_agent_run_metadata(
                    session,
                    assistant_message_id=assistant_message_id,
                    user_message=message,
                    assistant_message=persisted_answer,
                    agent_context=agent_context,
                    plan_only=bool(plan_only),
                )
            await _store.update(session)
            if resolved_mode == "agent":
                try:
                    await _persist_agent_task_snapshot(
                        session_id=session.id,
                        user_message=message,
                        assistant_message=persisted_answer,
                        plan_only=bool(plan_only),
                        agent_context=agent_context,
                    )
                except Exception as exc:
                    logger.warning("Failed to persist chat stream task snapshot", error=str(exc), session_id=session.id)
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "done",
                        "payload": {
                            "message": persisted_answer,
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
