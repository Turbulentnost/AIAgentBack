from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text

from app.core.config import settings
from app.db.session import engine
from app.schemas.common import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        environment=settings.ENVIRONMENT,
        version=settings.APP_VERSION,
    )


@router.get("/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    checks = {"database": "unknown"}
    try:
        async with engine.connect() as connection:
            await connection.scalar(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = "error"
        response = HealthResponse(
            status="error",
            environment=settings.ENVIRONMENT,
            version=settings.APP_VERSION,
            checks=checks,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=response.model_dump(),
        ) from exc

    return HealthResponse(
        status="ready",
        environment=settings.ENVIRONMENT,
        version=settings.APP_VERSION,
        checks=checks,
    )
