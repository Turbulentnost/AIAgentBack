from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.position import PositionRead
from app.services.position_service import PositionService

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("", response_model=list[PositionRead])
async def list_positions(db: DbSession, current_user: CurrentUser):
    _ = current_user
    names = await PositionService(db).list()
    return [PositionRead(name=name) for name in names]
