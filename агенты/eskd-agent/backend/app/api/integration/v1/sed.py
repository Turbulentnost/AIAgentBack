from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.integration.deps import IntegrationPrincipal, require_permission
from app.integration.sed_adapter import SedArchiveAdapter
from app.schemas.integration import SedArchivePayload, SedArchiveResponse

router = APIRouter(prefix="/api/v1/sed", tags=["integration-sed"])


@router.post("/archive", response_model=SedArchiveResponse)
async def archive_to_sed(
    body: SedArchivePayload,
    db: AsyncSession = Depends(get_db),
    _: IntegrationPrincipal = Depends(require_permission("sed:write")),
):
    try:
        return await SedArchiveAdapter(db).archive(body)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
