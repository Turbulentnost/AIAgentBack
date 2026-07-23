from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.tools as _tools  # noqa: F401
from app.api.v1.router import api_router
from app.core.config import Settings, settings
from app.core.logging import configure_logging, get_logger
from app.monitoring.metrics import setup_monitoring

configure_logging()
from app.tools.Outlook.ews_logging import configure_exchangelib_logging

configure_exchangelib_logging(verbose=False)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("app.startup", environment=settings.ENVIRONMENT)
    await _run_database_migrations()
    yield
    logger.info("app.shutdown")


def _upgrade_alembic_head() -> None:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    command.upgrade(cfg, "head")


async def _run_database_migrations() -> None:
    scripts = sorted((Path(__file__).resolve().parents[1] / "alembic" / "versions").glob("*.py"))
    if not scripts:
        logger.info(
            "app.migrations.skipped",
            reason="no local revision scripts in alembic/versions",
        )
        return

    try:
        await asyncio.to_thread(_upgrade_alembic_head)
        logger.info("app.migrations.upgraded")
    except Exception as exc:
        message = str(exc)
        if "Can't locate revision identified by" in message:
            logger.warning(
                "app.migrations.revision_mismatch",
                hint="DB alembic_version does not match local alembic/versions/*.py",
                output=message,
            )
            return
        logger.exception("app.migrations.failed", error=message)


def create_app(app_settings: Settings = settings) -> FastAPI:
    app = FastAPI(
        title=app_settings.PROJECT_NAME,
        version=app_settings.APP_VERSION,
        openapi_url=f"{app_settings.API_V1_PREFIX}/openapi.json",
        docs_url=app_settings.DOCS_URL,
        redoc_url=app_settings.REDOC_URL,
        lifespan=lifespan,
    )
    configure_cors(app, app_settings)
    setup_monitoring(app)
    app.include_router(api_router, prefix=app_settings.API_V1_PREFIX)

    @app.get("/", tags=["root"])
    async def root() -> dict[str, str]:
        return {
            "name": app_settings.PROJECT_NAME,
            "version": app_settings.APP_VERSION,
            "docs": app_settings.DOCS_URL,
            "api": app_settings.API_V1_PREFIX,
        }

    return app


def configure_cors(app: FastAPI, app_settings: Settings) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=app_settings.BACKEND_CORS_ALLOW_CREDENTIALS,
        allow_methods=app_settings.cors_allow_methods,
        allow_headers=app_settings.cors_allow_headers,
    )


app = create_app()
