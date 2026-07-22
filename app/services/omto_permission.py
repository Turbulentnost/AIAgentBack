"""Доступ к ролевым агентам ОМТО по должности пользователя.

Реализует требование: KPI-дашборд агента видит только человек, чья должность
(``User.position``) соответствует названию агента (например, дашборд «Агента
менеджера по закупкам» доступен пользователю с должностью «менеджер по закупкам»).

Три пути доступа (как у эталонного production_preparation_engineer_agent):
1) суперпользователь;
2) совпадение строки должности с маркером агента (case-insensitive, ё→е);
3) обычный RBAC-грант ``agents.<slug>.run`` (роль/подразделение/персональный).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.omto_role_agents.catalog import OMTO_AGENT_SLUGS, OMTO_AGENTS, get_spec
from app.models.agent import Agent
from app.models.user import User
from app.services.permission_service import PermissionService


def _normalize(position: str | None) -> str:
    if not position:
        return ""
    return " ".join(position.casefold().replace("ё", "е").split())


def position_matches_agent(position: str | None, slug: str) -> bool:
    spec = get_spec(slug)
    if spec is None:
        return False
    normalized = _normalize(position)
    if not normalized:
        return False
    return any(marker in normalized for marker in spec.position_markers)


async def can_access_omto_agent(db: AsyncSession, user: User, slug: str) -> bool:
    if slug not in OMTO_AGENTS:
        return False
    if user.is_superuser or position_matches_agent(user.position, slug):
        return True
    agent = await db.scalar(select(Agent).where(Agent.slug == slug))
    if agent is None:
        return False
    return await PermissionService(db).can_access_agent(user, agent.id, action="run")


async def accessible_omto_agents(db: AsyncSession, user: User) -> list[str]:
    """Список slug'ов агентов ОМТО, доступных пользователю (для фронта)."""
    result: list[str] = []
    for slug in OMTO_AGENT_SLUGS:
        if await can_access_omto_agent(db, user, slug):
            result.append(slug)
    return result


async def append_omto_agents(db: AsyncSession, user: User, agents: list) -> list:
    """Дописывает доступные пользователю агенты ОМТО в каталог ``/agents/available``."""
    existing_ids = {item.id for item in agents}
    additions = []
    for slug in await accessible_omto_agents(db, user):
        agent = await db.scalar(select(Agent).where(Agent.slug == slug))
        if agent is None or agent.id in existing_ids:
            continue
        existing_ids.add(agent.id)
        additions.append(agent)
    if not additions:
        return agents
    return sorted([*agents, *additions], key=lambda item: item.name)


__all__ = [
    "accessible_omto_agents",
    "append_omto_agents",
    "can_access_omto_agent",
    "position_matches_agent",
]
