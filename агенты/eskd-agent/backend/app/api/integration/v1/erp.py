from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.integration.deps import IntegrationPrincipal, require_permission
from app.integration.erp_adapter import ErpAdapter
from app.schemas.integration import ErpReadinessResponse, ErpReadinessUpdate

router = APIRouter(prefix="/api/v1/erp", tags=["integration-erp"])


@router.post("/context")
async def update_erp_context(
    body: ErpReadinessUpdate,
    db: AsyncSession = Depends(get_db),
    _: IntegrationPrincipal = Depends(require_permission("erp:read")),
):
    doc = await ErpAdapter(db).upsert_context(body)
    return {"document_id": doc.external_document_id, "updated": True}


@router.get("/readiness/{document_id}", response_model=ErpReadinessResponse)
async def get_erp_readiness(
    document_id: str,
    source_system: str = "1c",
    db: AsyncSession = Depends(get_db),
    _: IntegrationPrincipal = Depends(require_permission("erp:read")),
):
    return await ErpAdapter(db).get_readiness(document_id=document_id, source_system=source_system)
