from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.services.user_service import EskdActor, UserService


@dataclass
class OptionalEskdActor:
    actor: EskdActor | None


async def get_optional_eskd_actor(
    db: AsyncSession = Depends(get_db),
    x_dev_user: str | None = Header(default=None, alias="X-Dev-User"),
) -> OptionalEskdActor:
    actor = await UserService(db).resolve_actor(x_dev_user)
    return OptionalEskdActor(actor=actor)
