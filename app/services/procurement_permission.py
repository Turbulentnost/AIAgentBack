from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.user import User
from app.services.permission_service import PermissionService

PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG = "production_preparation_engineer_agent"
OMTO_SUPPORT_MANAGER_AGENT_SLUG = "omto_support_manager_agent"
OTK_HEAD_AGENT_SLUG = "otk_head_agent"
QUALITY_ENGINEER_AGENT_SLUG = "quality_engineer_agent"
QUALITY_DEPUTY_DIRECTOR_AGENT_SLUG = "quality_deputy_director_agent"
QUALITY_KPI_AGENT_SLUG = "quality_kpi_agent"

_ENGINEER_POSITION_MARKERS = (
    "инженер по подготовке производства",
    "инженер спп",
)
_OMTO_POSITION_MARKERS = (
    "менеджер по сопровождению омто",
    "менеджер омто",
    "омто",
)
_OTK_HEAD_MARKERS = (
    "начальник отк",
    "начальник отдела технического контроля",
)
_QUALITY_ENGINEER_MARKERS = (
    "инженер по качеству",
    "инженер отк",
)
_QUALITY_DEPUTY_MARKERS = (
    "заместитель директора по качеству",
    "зам директора по качеству",
    "заместитель технического директора по качеству",
    "зтд по качеству",
    "здк",
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
    if is_production_preparation_engineer_position(normalized):
        return False
    if is_quality_engineer_position(normalized) or is_otk_head_position(normalized):
        return False
    return any(marker in normalized for marker in _OMTO_POSITION_MARKERS)


def is_otk_head_position(position: str | None) -> bool:
    normalized = _normalize_position(position)
    if not normalized:
        return False
    return any(marker in normalized for marker in _OTK_HEAD_MARKERS)


def is_quality_engineer_position(position: str | None) -> bool:
    normalized = _normalize_position(position)
    if not normalized:
        return False
    if is_production_preparation_engineer_position(normalized):
        return False
    return any(marker in normalized for marker in _QUALITY_ENGINEER_MARKERS)


def is_quality_deputy_director_position(position: str | None) -> bool:
    normalized = _normalize_position(position)
    if not normalized:
        return False
    return any(marker in normalized for marker in _QUALITY_DEPUTY_MARKERS)


async def can_access_procurement_orchestrator(db: AsyncSession, user: User) -> bool:
    """Orchestrator UI/API is visible only to the system superuser."""
    _ = db
    return bool(user.is_superuser)


async def can_refresh_procurement_orchestrator(db: AsyncSession, user: User) -> bool:
    return await can_access_procurement_orchestrator(db, user)


async def _can_access_by_slug_or_position(
    db: AsyncSession,
    user: User,
    slug: str,
    position_ok: bool,
) -> bool:
    if user.is_superuser or position_ok:
        return True
    agent = await db.scalar(select(Agent).where(Agent.slug == slug))
    if agent is None:
        return False
    return await PermissionService(db).can_access_agent(user, agent.id, action="run")


async def can_access_production_preparation_engineer(
    db: AsyncSession,
    user: User,
) -> bool:
    return await _can_access_by_slug_or_position(
        db,
        user,
        PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG,
        is_production_preparation_engineer_position(user.position),
    )


async def can_access_omto_support_manager(
    db: AsyncSession,
    user: User,
) -> bool:
    return await _can_access_by_slug_or_position(
        db,
        user,
        OMTO_SUPPORT_MANAGER_AGENT_SLUG,
        is_omto_support_manager_position(user.position),
    )


async def can_access_otk_head(db: AsyncSession, user: User) -> bool:
    return await _can_access_by_slug_or_position(
        db,
        user,
        OTK_HEAD_AGENT_SLUG,
        is_otk_head_position(user.position),
    )


async def can_access_quality_engineer(db: AsyncSession, user: User) -> bool:
    return await _can_access_by_slug_or_position(
        db,
        user,
        QUALITY_ENGINEER_AGENT_SLUG,
        is_quality_engineer_position(user.position),
    )


async def can_access_quality_deputy_director(db: AsyncSession, user: User) -> bool:
    return await _can_access_by_slug_or_position(
        db,
        user,
        QUALITY_DEPUTY_DIRECTOR_AGENT_SLUG,
        is_quality_deputy_director_position(user.position),
    )


async def can_access_quality_kpi(db: AsyncSession, user: User) -> bool:
    if user.is_superuser or is_quality_deputy_director_position(user.position):
        return True
    return await _can_access_by_slug_or_position(
        db,
        user,
        QUALITY_KPI_AGENT_SLUG,
        False,
    )


async def _append_agent_by_slug(db: AsyncSession, agents: list, slug: str) -> list:
    agent = await db.scalar(select(Agent).where(Agent.slug == slug))
    if agent is None or any(item.id == agent.id for item in agents):
        return agents
    return sorted([*agents, agent], key=lambda item: item.name)


async def append_production_preparation_engineer_agent(
    db: AsyncSession,
    user: User,
    agents: list,
) -> list:
    if not await can_access_production_preparation_engineer(db, user):
        return agents
    return await _append_agent_by_slug(db, agents, PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG)


async def append_omto_support_manager_agent(
    db: AsyncSession,
    user: User,
    agents: list,
) -> list:
    if not await can_access_omto_support_manager(db, user):
        return agents
    return await _append_agent_by_slug(db, agents, OMTO_SUPPORT_MANAGER_AGENT_SLUG)


async def append_otk_head_agent(db: AsyncSession, user: User, agents: list) -> list:
    if not await can_access_otk_head(db, user):
        return agents
    return await _append_agent_by_slug(db, agents, OTK_HEAD_AGENT_SLUG)


async def append_quality_engineer_agent(db: AsyncSession, user: User, agents: list) -> list:
    if not await can_access_quality_engineer(db, user):
        return agents
    return await _append_agent_by_slug(db, agents, QUALITY_ENGINEER_AGENT_SLUG)


async def append_quality_deputy_director_agent(
    db: AsyncSession,
    user: User,
    agents: list,
) -> list:
    if not await can_access_quality_deputy_director(db, user):
        return agents
    return await _append_agent_by_slug(db, agents, QUALITY_DEPUTY_DIRECTOR_AGENT_SLUG)


async def append_quality_kpi_agent(db: AsyncSession, user: User, agents: list) -> list:
    if not await can_access_quality_kpi(db, user):
        return agents
    return await _append_agent_by_slug(db, agents, QUALITY_KPI_AGENT_SLUG)


__all__ = [
    "OMTO_SUPPORT_MANAGER_AGENT_SLUG",
    "OTK_HEAD_AGENT_SLUG",
    "PRODUCTION_PREPARATION_ENGINEER_AGENT_SLUG",
    "QUALITY_DEPUTY_DIRECTOR_AGENT_SLUG",
    "QUALITY_ENGINEER_AGENT_SLUG",
    "QUALITY_KPI_AGENT_SLUG",
    "append_omto_support_manager_agent",
    "append_otk_head_agent",
    "append_production_preparation_engineer_agent",
    "append_quality_deputy_director_agent",
    "append_quality_engineer_agent",
    "append_quality_kpi_agent",
    "can_access_omto_support_manager",
    "can_access_otk_head",
    "can_access_production_preparation_engineer",
    "can_access_procurement_orchestrator",
    "can_access_quality_deputy_director",
    "can_access_quality_engineer",
    "can_access_quality_kpi",
    "can_refresh_procurement_orchestrator",
    "is_omto_support_manager_position",
    "is_otk_head_position",
    "is_production_preparation_engineer_position",
    "is_quality_deputy_director_position",
    "is_quality_engineer_position",
]
