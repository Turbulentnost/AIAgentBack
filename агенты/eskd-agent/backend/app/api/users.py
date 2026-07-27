from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.user import EskdUserListResponse, EskdUserRead
from app.services.user_service import UserService

router = APIRouter(prefix="/api/v1/eskd/users", tags=["eskd-users"])


@router.get("", response_model=EskdUserListResponse)
async def list_users(
    db: AsyncSession = Depends(get_db),
    role: str | None = Query(default="ESKD_OTK"),
):
    rows = await UserService(db).list_active(role=role)
    return EskdUserListResponse(
        items=[
            EskdUserRead(
                id=row.id,
                login=row.login,
                display_name=row.display_name,
                role=row.role,
                department=row.department,
            )
            for row in rows
        ]
    )
