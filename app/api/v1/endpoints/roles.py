from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models.user import Role
from app.schemas.role import RoleRead

router = APIRouter(prefix="/roles", tags=["roles"])


@router.get("", response_model=list[RoleRead])
async def list_roles(db: DbSession, current_user: CurrentUser):
    _ = current_user
    result = await db.execute(select(Role).order_by(Role.name.asc()))
    return list(result.scalars().all())
