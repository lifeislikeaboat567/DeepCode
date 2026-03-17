"""Chat routes for single-turn and streaming agent interactions."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from deepcode.agents.base import BaseAgent
from deepcode.api.models import AgentStep, ChatRequest, ChatResponse, CodeArtifact
from deepcode.llm.factory import create_llm_client
from deepcode.logging_config import get_logger
from deepcode.storage import Message, SessionStore
from deepcode.tools import CodeExecutorTool, FileManagerTool, ShellTool

router = APIRouter()
logger = get_logger(__name__)
_store = SessionStore()


def _build_agent() -> BaseAgent:
    """Create a fresh agent with the configured LLM and standard tools."""
    llm = create_llm_client()
    tools = [
        CodeExecutorTool(),
        FileManagerTool(),
        ShellTool(),
    ]
    return BaseAgent(llm=llm, tools=tools)


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

    # Run the agent
    agent = _build_agent()
    try:
        result = await agent.run(request.message)
    except Exception as exc:
        logger.error("Agent run failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent error: {exc}",
        ) from exc

    # Persist messages
    session.messages.append(Message(role="user", content=request.message))
    session.messages.append(Message(role="assistant", content=result.answer))
    await _store.update(session)

    return ChatResponse(
        session_id=session.id,
        message=result.answer,
        code_artifacts=[
            CodeArtifact(
                filename=a.get("filename", "output.py"),
                content=a.get("content", ""),
            )
            for a in result.code_artifacts
        ],
        steps=[
            AgentStep(
                thought=s.thought,
                action=s.action,
                observation=s.observation,
            )
            for s in result.steps
        ],
        success=result.success,
        error=result.error,
    )


@router.get("/stream", tags=["Chat"])
async def chat_stream(message: str, session_id: str | None = None) -> StreamingResponse:
    """Stream an agent response as Server-Sent Events (SSE).

    Args:
        message: The user's message.
        session_id: Optional existing session ID.
    """

    async def event_generator():
        agent = _build_agent()
        try:
            async for chunk in agent.stream_run(message):
                # SSE format
                data = chunk.replace("\n", "\\n")
                yield f"data: {data}\n\n"
        except Exception as exc:
            yield f"data: Error: {exc}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
