from __future__ import annotations

import asyncio
import subprocess
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
    try:
        from app.services.onec_db_schema import ensure_onec_agent_tables

        await ensure_onec_agent_tables()
    except Exception as exc:
        logger.warning("app.onec_tables.ensure_failed", error=str(exc))

    stop_onec_sync = asyncio.Event()
    onec_sync_task: asyncio.Task | None = None
    if settings.ONEC_DAILY_SYNC_ENABLED and settings.ONEC_INPROCESS_SYNC_ENABLED:
        from app.services.onec_sync_scheduler import onec_sync_scheduler_loop

        onec_sync_task = asyncio.create_task(onec_sync_scheduler_loop(stop_onec_sync))

    yield

    stop_onec_sync.set()
    if onec_sync_task is not None:
        onec_sync_task.cancel()
        try:
            await onec_sync_task
        except asyncio.CancelledError:
            pass
    logger.info("app.shutdown")


async def _run_database_migrations() -> None:
    scripts = sorted((Path(__file__).resolve().parents[1] / "alembic" / "versions").glob("*.py"))
    if not scripts:
        logger.info(
            "app.migrations.skipped",
            reason="no local revision scripts in alembic/versions",
        )
        return

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            output = f"{result.stdout or ''}{result.stderr or ''}".strip()
            if "Can't locate revision identified by" in output:
                logger.warning(
                    "app.migrations.revision_mismatch",
                    hint="DB alembic_version does not match local alembic/versions/*.py",
                    output=output,
                )
                return
            raise RuntimeError(output or f"alembic exit code {result.returncode}")
        logger.info("app.migrations.upgraded")
    except Exception as exc:
        logger.exception("app.migrations.failed", error=str(exc))


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
        expose_headers=["Content-Disposition", "X-Aveon-Analysis-Source"],
    )


app = create_app()
