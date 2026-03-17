"""Session management routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from deepcode.api.models import (
    CreateSessionRequest,
    MessageSchema,
    SessionDetailSchema,
    SessionSchema,
)
from deepcode.exceptions import SessionNotFoundError
from deepcode.storage import SessionStore

router = APIRouter()
_store = SessionStore()


def _to_schema(session: object) -> SessionSchema:
    """Convert a :class:`Session` to its API schema."""
    from deepcode.storage import Session  # local import to avoid circular

    s: Session = session  # type: ignore[assignment]
    return SessionSchema(
        id=s.id,
        name=s.name,
        message_count=len(s.messages),
        created_at=s.created_at,
        updated_at=s.updated_at,
        metadata=s.metadata,
    )


@router.post("", response_model=SessionSchema, status_code=status.HTTP_201_CREATED, tags=["Sessions"])
async def create_session(request: CreateSessionRequest) -> SessionSchema:
    """Create a new conversation session."""
    session = await _store.create(name=request.name)
    return _to_schema(session)


@router.get("", response_model=list[SessionSchema], tags=["Sessions"])
async def list_sessions() -> list[SessionSchema]:
    """List all sessions ordered by most recently updated."""
    sessions = await _store.list_all()
    return [_to_schema(s) for s in sessions]


@router.get("/{session_id}", response_model=SessionDetailSchema, tags=["Sessions"])
async def get_session(session_id: str) -> SessionDetailSchema:
    """Get a session including its full message history."""
    try:
        session = await _store.get(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return SessionDetailSchema(
        id=session.id,
        name=session.name,
        message_count=len(session.messages),
        created_at=session.created_at,
        updated_at=session.updated_at,
        metadata=session.metadata,
        messages=[
            MessageSchema(role=m.role, content=m.content, created_at=m.created_at)
            for m in session.messages
        ],
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Sessions"])
async def delete_session(session_id: str) -> None:
    """Delete a session and all its messages."""
    try:
        await _store.delete(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
