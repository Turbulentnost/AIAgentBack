from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.position import PositionRead
from app.services.position_service import PositionService

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("", response_model=list[PositionRead])
async def list_positions(
    db: DbSession,
    current_user: CurrentUser,
    search: str | None = None,
    department_id: uuid.UUID | None = None,
    limit: int = 1000,
    active_only: bool = True,
):
    _ = current_user
    positions = await PositionService(db).list(
        search=search,
        department_id=department_id,
        limit=limit,
        active_only=active_only,
        with_departments=True,
    )
    service = PositionService(db)
    return [service.to_read(position) for position in positions]


@router.get("/{position_id}", response_model=PositionRead)
async def get_position(
    db: DbSession,
    current_user: CurrentUser,
    position_id: uuid.UUID,
):
    _ = current_user
    position = await PositionService(db).get(position_id)
    if position is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Должность не найдена")
    return PositionService(db).to_read(position)
