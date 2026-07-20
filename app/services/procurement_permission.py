from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.user import User
from app.services.permission_service import PermissionService

PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG = "production_preparation_engineer_agent"
OMTO_SUPPORT_MANAGER_AGENT_SLUG = "omto_support_manager_agent"

_ENGINEER_POSITION_MARKERS = (
    "инженер по подготовке производства",
    "инженер спп",
)
_OMTO_POSITION_MARKERS = (
    "менеджер по сопровождению омто",
    "менеджер омто",
    "омто",
)


def _normalize_position(position: str | None) -> str:
    if not position:
        return ""
    return " ".join(position.casefold().replace("ё", "е").split())


def is_production_preparation_engineer_position(position: str | None) -> bool:
    normalized = _normalize_position(position)
    if not normalized:
        return False
    return any(marker in normalized for marker in _ENGINEER_POSITION_MARKERS)


def is_omto_support_manager_position(position: str | None) -> bool:
    normalized = _normalize_position(position)
    if not normalized:
        return False
    # Avoid matching engineer titles that accidentally contain substrings.
    if is_production_preparation_engineer_position(normalized):
        return False
    return any(marker in normalized for marker in _OMTO_POSITION_MARKERS)


async def can_access_procurement_orchestrator(db: AsyncSession, user: User) -> bool:
    """Orchestrator UI/API is visible only to the system superuser."""
    _ = db
    return bool(user.is_superuser)


async def can_refresh_procurement_orchestrator(db: AsyncSession, user: User) -> bool:
    return await can_access_procurement_orchestrator(db, user)


async def can_access_production_preparation_engineer(
    db: AsyncSession,
    user: User,
) -> bool:
    if user.is_superuser or is_production_preparation_engineer_position(user.position):
        return True
    agent = await db.scalar(
        select(Agent).where(Agent.slug == PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG)
    )
    if agent is None:
        return False
    return await PermissionService(db).can_access_agent(user, agent.id, action="run")


async def can_access_omto_support_manager(
    db: AsyncSession,
    user: User,
) -> bool:
    if user.is_superuser or is_omto_support_manager_position(user.position):
        return True
    agent = await db.scalar(
        select(Agent).where(Agent.slug == OMTO_SUPPORT_MANAGER_AGENT_SLUG)
    )
    if agent is None:
        return False
    return await PermissionService(db).can_access_agent(user, agent.id, action="run")


async def append_production_preparation_engineer_agent(
    db: AsyncSession,
    user: User,
    agents: list,
) -> list:
    if not await can_access_production_preparation_engineer(db, user):
        return agents
    agent = await db.scalar(
        select(Agent).where(Agent.slug == PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG)
    )
    if agent is None or any(item.id == agent.id for item in agents):
        return agents
    return sorted([*agents, agent], key=lambda item: item.name)


async def append_omto_support_manager_agent(
    db: AsyncSession,
    user: User,
    agents: list,
) -> list:
    if not await can_access_omto_support_manager(db, user):
        return agents
    agent = await db.scalar(
        select(Agent).where(Agent.slug == OMTO_SUPPORT_MANAGER_AGENT_SLUG)
    )
    if agent is None or any(item.id == agent.id for item in agents):
        return agents
    return sorted([*agents, agent], key=lambda item: item.name)


__all__ = [
    "OMTO_SUPPORT_MANAGER_AGENT_SLUG",
    "PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG",
    "append_omto_support_manager_agent",
    "append_production_preparation_engineer_agent",
    "can_access_omto_support_manager",
    "can_access_production_preparation_engineer",
    "can_access_procurement_orchestrator",
    "can_refresh_procurement_orchestrator",
    "is_omto_support_manager_position",
    "is_production_preparation_engineer_position",
]
