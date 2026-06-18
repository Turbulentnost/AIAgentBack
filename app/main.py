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
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("app.startup", environment=settings.ENVIRONMENT)
    await _run_database_migrations()
    yield
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
    )


app = create_app()
