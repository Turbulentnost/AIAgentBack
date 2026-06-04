from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.monitoring.metrics import setup_monitoring

configure_logging()
logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("app.startup", environment=settings.ENVIRONMENT)
    yield
    logger.info("app.shutdown")

app = FastAPI(title=settings.PROJECT_NAME, version="0.1.0", openapi_url=f"{settings.API_V1_PREFIX}/openapi.json", docs_url="/docs", redoc_url="/redoc", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.BACKEND_CORS_ORIGINS, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
setup_monitoring(app)
app.include_router(api_router, prefix=settings.API_V1_PREFIX)

@app.get("/", tags=["root"])
async def root() -> dict[str, str]:
    return {"name": settings.PROJECT_NAME, "docs": "/docs", "api": settings.API_V1_PREFIX}
