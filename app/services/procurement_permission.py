from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.user import User
from app.services.permission_service import PermissionService

PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG = "production_preparation_engineer_agent"
PRODUCTION_DISPATCHER_AGENT_SLUG = "production_dispatcher_agent"
WAREHOUSE_PICKER_AGENT_SLUG = "warehouse_picker_agent"
_ENGINEER_POSITION_MARKERS = (
    "инженер по подготовке производства",
    "инженер спп",
)
_DISPATCHER_POSITION_MARKERS = (
    "диспетчер производства",
    "главный диспетчер",
)
_PICKER_POSITION_MARKERS = (
    "кладовщик-комплектовщик",
    "кладовщик комплектовщик",
)


def is_production_preparation_engineer_position(position: str | None) -> bool:
    if not position:
        return False
    normalized = " ".join(position.casefold().replace("ё", "е").split())
    return any(marker in normalized for marker in _ENGINEER_POSITION_MARKERS)


def is_production_dispatcher_position(position: str | None) -> bool:
    if not position:
        return False
    normalized = " ".join(position.casefold().replace("ё", "е").split())
    return any(marker in normalized for marker in _DISPATCHER_POSITION_MARKERS)


def is_warehouse_picker_position(position: str | None) -> bool:
    if not position:
        return False
    normalized = " ".join(position.casefold().replace("ё", "е").replace("-", " ").split())
    return any(marker.replace("-", " ") in normalized for marker in _PICKER_POSITION_MARKERS)


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


async def can_access_production_dispatcher(
    db: AsyncSession,
    user: User,
) -> bool:
    if user.is_superuser or is_production_dispatcher_position(user.position):
        return True
    agent = await db.scalar(
        select(Agent).where(Agent.slug == PRODUCTION_DISPATCHER_AGENT_SLUG)
    )
    if agent is None:
        return False
    return await PermissionService(db).can_access_agent(user, agent.id, action="run")


async def append_production_dispatcher_agent(
    db: AsyncSession,
    user: User,
    agents: list,
) -> list:
    if not await can_access_production_dispatcher(db, user):
        return agents
    agent = await db.scalar(
        select(Agent).where(Agent.slug == PRODUCTION_DISPATCHER_AGENT_SLUG)
    )
    if agent is None or any(item.id == agent.id for item in agents):
        return agents
    return sorted([*agents, agent], key=lambda item: item.name)


async def can_access_warehouse_picker(
    db: AsyncSession,
    user: User,
) -> bool:
    if user.is_superuser or is_warehouse_picker_position(user.position):
        return True
    agent = await db.scalar(
        select(Agent).where(Agent.slug == WAREHOUSE_PICKER_AGENT_SLUG)
    )
    if agent is None:
        return False
    return await PermissionService(db).can_access_agent(user, agent.id, action="run")


async def append_warehouse_picker_agent(
    db: AsyncSession,
    user: User,
    agents: list,
) -> list:
    if not await can_access_warehouse_picker(db, user):
        return agents
    agent = await db.scalar(
        select(Agent).where(Agent.slug == WAREHOUSE_PICKER_AGENT_SLUG)
    )
    if agent is None or any(item.id == agent.id for item in agents):
        return agents
    return sorted([*agents, agent], key=lambda item: item.name)


__all__ = [
    "WAREHOUSE_PICKER_AGENT_SLUG",
    "append_production_dispatcher_agent",
    "append_production_preparation_engineer_agent",
    "append_warehouse_picker_agent",
    "can_access_production_dispatcher",
    "can_access_production_preparation_engineer",
    "can_access_procurement_orchestrator",
    "can_access_warehouse_picker",
    "can_refresh_procurement_orchestrator",
    "is_production_dispatcher_position",
    "is_production_preparation_engineer_position",
    "is_warehouse_picker_position",
]
