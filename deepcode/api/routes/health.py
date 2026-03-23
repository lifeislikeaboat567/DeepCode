"""Health check route."""

from __future__ import annotations

from fastapi import APIRouter

from deepcode import __version__
from deepcode.api.models import HealthResponse
from deepcode.config import get_settings

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check() -> HealthResponse:
    """Return service health status and configuration summary."""
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=__version__,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model,
    )
