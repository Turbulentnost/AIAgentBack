from __future__ import annotations
from fastapi import APIRouter
from app.core.config import settings
from app.schemas.common import HealthResponse
router = APIRouter(tags=["health"])
@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", environment=settings.ENVIRONMENT, version="0.1.0")
@router.get("/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    return HealthResponse(status="ready", environment=settings.ENVIRONMENT, version="0.1.0")
