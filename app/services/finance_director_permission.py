from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.finance_director_agent.config import (
    FINANCE_DIRECTOR_AGENT_ID,
    POSITION_MARKERS,
)
from app.models.agent import Agent
from app.models.user import User
from app.services.permission_service import PermissionService


def is_finance_director_position(position: str | None) -> bool:
    if not position:
        return False
    normalized = " ".join(position.casefold().replace("ё", "е").split())
    return any(marker in normalized for marker in POSITION_MARKERS)


async def can_access_finance_director_agent(db: AsyncSession, user: User) -> bool:
    if user.is_superuser or is_finance_director_position(user.position):
        return True
    agent = await db.scalar(
        select(Agent).where(Agent.slug == FINANCE_DIRECTOR_AGENT_ID)
    )
    if agent is None:
        return False
    return await PermissionService(db).can_access_agent(user, agent.id, action="run")


async def append_finance_director_agent(
    db: AsyncSession,
    user: User,
    agents: list,
) -> list:
    if not await can_access_finance_director_agent(db, user):
        return agents
    agent = await db.scalar(
        select(Agent).where(Agent.slug == FINANCE_DIRECTOR_AGENT_ID)
    )
    if agent is None or any(item.id == agent.id for item in agents):
        return agents
    return sorted([*agents, agent], key=lambda item: item.name)


__all__ = [
    "append_finance_director_agent",
    "can_access_finance_director_agent",
    "is_finance_director_position",
]
