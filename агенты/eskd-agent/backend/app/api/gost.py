from __future__ import annotations

from fastapi import APIRouter

from app.gost.catalog import gost_catalog

router = APIRouter(prefix="/api/v1/eskd", tags=["eskd-gost"])


@router.get("/gost-catalog")
async def get_gost_catalog() -> dict:
    return {"items": gost_catalog()}
