from __future__ import annotations

import logging
import sys
import structlog
from app.core.config import settings


def configure_logging() -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.DEBUG if settings.DEBUG else logging.INFO)
    renderer = structlog.dev.ConsoleRenderer() if settings.ENVIRONMENT == "dev" else structlog.processors.JSONRenderer()
    structlog.configure(processors=[structlog.contextvars.merge_contextvars, structlog.processors.add_log_level, structlog.processors.TimeStamper(fmt="iso"), renderer], logger_factory=structlog.PrintLoggerFactory(), cache_logger_on_first_use=True)


def get_logger(name: str | None = None):
    return structlog.get_logger(name)
