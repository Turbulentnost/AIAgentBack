"""Desktop sidecar FastAPI app — только auth + document-analysis + health."""

from __future__ import annotations

from app_desktop.bootstrap_env import load_desktop_env

load_desktop_env()

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints import agents, auth, health
from app.core.config import Settings, settings
from app.core.logging import configure_logging, get_logger
from app.monitoring.metrics import setup_monitoring

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("app_desktop.startup", environment=settings.ENVIRONMENT)
    try:
        from app.db.session import AsyncSessionLocal
        from app.services.aveon_desktop_users import ensure_aveon_desktop_users

        async with AsyncSessionLocal() as session:
            ensured = await ensure_aveon_desktop_users(session)
            if ensured:
                logger.info("app_desktop.users_ensured", count=len(ensured))
    except Exception as exc:
        logger.warning("app_desktop.users.ensure_failed", error=str(exc))

    try:
        from app.services.onec_db_schema import ensure_onec_agent_tables

        await ensure_onec_agent_tables()
    except Exception as exc:
        logger.warning("app_desktop.onec_tables.ensure_failed", error=str(exc))

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
    logger.info("app_desktop.shutdown")


def create_desktop_app(app_settings: Settings = settings) -> FastAPI:
    app = FastAPI(
        title=f"{app_settings.PROJECT_NAME} (Desktop)",
        version=app_settings.APP_VERSION,
        openapi_url=f"{app_settings.API_V1_PREFIX}/openapi.json",
        docs_url=app_settings.DOCS_URL,
        redoc_url=app_settings.REDOC_URL,
        lifespan=lifespan,
    )
    configure_cors(app, app_settings)
    setup_monitoring(app)

    api_router = APIRouter()
    api_router.include_router(health.router)
    api_router.include_router(auth.router)
    api_router.include_router(agents.router)
    app.include_router(api_router, prefix=app_settings.API_V1_PREFIX)

    @app.get("/", tags=["root"])
    async def root() -> dict[str, str]:
        return {
            "name": app_settings.PROJECT_NAME,
            "mode": "desktop",
            "version": app_settings.APP_VERSION,
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


app = create_desktop_app()
