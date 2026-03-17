"""Structured logging setup for DeepCode Agent."""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(debug: bool = False) -> None:
    """Configure structlog for the application.

    Args:
        debug: When ``True``, set log level to DEBUG and enable pretty-printing.
    """
    level = logging.DEBUG if debug else logging.INFO

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer() if debug else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """Return a named bound logger.

    Args:
        name: Logger name, typically ``__name__``.

    Returns:
        A structlog bound logger instance.
    """
    return structlog.get_logger(name)
