from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

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
    yield
    logger.info("app.shutdown")


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
