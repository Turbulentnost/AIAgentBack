from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.accountant_agent.config import (
    ACCOUNTANT_AGENT_ID,
    CHIEF_EXCLUDE_MARKERS,
    POSITION_MARKERS,
)
from app.models.agent import Agent
from app.models.user import User
from app.services.permission_service import PermissionService


def is_accountant_position(position: str | None) -> bool:
    if not position:
        return False
    normalized = " ".join(position.casefold().replace("ё", "е").split())
    if any(marker in normalized for marker in CHIEF_EXCLUDE_MARKERS):
        return False
    return any(marker in normalized for marker in POSITION_MARKERS)


async def can_access_accountant_agent(db: AsyncSession, user: User) -> bool:
    if user.is_superuser or is_accountant_position(user.position):
        return True
    agent = await db.scalar(select(Agent).where(Agent.slug == ACCOUNTANT_AGENT_ID))
    if agent is None:
        return False
    return await PermissionService(db).can_access_agent(user, agent.id, action="run")


async def append_accountant_agent(
    db: AsyncSession,
    user: User,
    agents: list,
) -> list:
    if not await can_access_accountant_agent(db, user):
        return agents
    agent = await db.scalar(select(Agent).where(Agent.slug == ACCOUNTANT_AGENT_ID))
    if agent is None or any(item.id == agent.id for item in agents):
        return agents
    return sorted([*agents, agent], key=lambda item: item.name)


__all__ = [
    "append_accountant_agent",
    "can_access_accountant_agent",
    "is_accountant_position",
]
