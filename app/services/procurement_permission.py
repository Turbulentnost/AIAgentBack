from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def can_access_procurement_orchestrator(db: AsyncSession, user: User) -> bool:
    """Orchestrator UI/API is visible only to the system superuser."""
    _ = db
    return bool(user.is_superuser)


async def can_refresh_procurement_orchestrator(db: AsyncSession, user: User) -> bool:
    return await can_access_procurement_orchestrator(db, user)


__all__ = [
    "can_access_procurement_orchestrator",
    "can_refresh_procurement_orchestrator",
]
