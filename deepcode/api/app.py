"""FastAPI application factory for DeepCode Agent."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from deepcode import __version__
from deepcode.config import get_settings
from deepcode.logging_config import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: initialise resources on startup."""
    settings = get_settings()
    configure_logging(debug=settings.debug)
    settings.ensure_data_dir()
    logger.info(
        "DeepCode Agent starting",
        version=__version__,
        provider=settings.llm_provider,
        model=settings.llm_model,
    )
    yield
    logger.info("DeepCode Agent shutting down")


def create_app() -> FastAPI:
    """Construct and return the configured :class:`fastapi.FastAPI` application.

    Returns:
        The FastAPI application with all routes registered.
    """
    from deepcode.api.routes.chat import router as chat_router
    from deepcode.api.routes.health import router as health_router
    from deepcode.api.routes.platforms import router as platforms_router
    from deepcode.api.routes.sessions import router as sessions_router
    from deepcode.api.routes.tasks import router as tasks_router

    settings = get_settings()

    app = FastAPI(
        title="DeepCode Agent API",
        description=(
            "AI-powered software engineering assistant. "
            "Send tasks, get code, review, and tests back."
        ),
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS – permissive for local development; tighten in production
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.debug else ["http://localhost:8501"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routers
    prefix = "/api/v1"
    app.include_router(health_router, prefix=prefix)
    app.include_router(chat_router, prefix=f"{prefix}/chat")
    app.include_router(platforms_router, prefix=f"{prefix}/platforms")
    app.include_router(sessions_router, prefix=f"{prefix}/sessions")
    app.include_router(tasks_router, prefix=f"{prefix}/tasks")

    return app
