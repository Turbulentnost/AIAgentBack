from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Role, User, user_roles

ND_CONTROL_AGENT_SLUG = "nd_control_agent"
ND_CONTROL_RUN_PERMISSION = "agents.nd_control_agent.run"

_QUALITY_DEPUTY_POSITION_RE = re.compile(
    r"заместител\w*\s+техническ\w*\s+директор\w*\s+по\s+качеств\w*",
    re.IGNORECASE,
)


def is_quality_deputy_position(position: str | None) -> bool:
    if not position or not position.strip():
        return False
    return bool(_QUALITY_DEPUTY_POSITION_RE.search(position.strip()))


async def user_has_admin_role(db: AsyncSession, user: User) -> bool:
    if user.role is not None and user.role.code == "admin":
        return True
    result = await db.execute(
        select(Role.code)
        .join(user_roles, user_roles.c.role_id == Role.id)
        .where(user_roles.c.user_id == user.id)
    )
    return "admin" in {row[0] for row in result.all()}


async def can_manage_nd_control_departments(db: AsyncSession, user: User) -> bool:
    if user.is_superuser:
        return True
    if await user_has_admin_role(db, user):
        return True
    return is_quality_deputy_position(user.position)


async def can_access_nd_control_agent(db: AsyncSession, user: User) -> bool:
    if user.is_superuser:
        return True
    if await user_has_admin_role(db, user):
        return True
    if is_quality_deputy_position(user.position):
        return True

    from app.models.agent import Agent
    from app.services.permission_service import PermissionService

    agent = await db.scalar(select(Agent).where(Agent.slug == ND_CONTROL_AGENT_SLUG))
    if agent is None:
        return False
    return await PermissionService(db).can_access_agent(user, agent.id, action="run")


async def append_nd_control_agent_for_quality_deputy(
    db: AsyncSession,
    user: User,
    agents: list,
) -> list:
    """Добавляет nd_control_agent в каталог для зам. тех. директора по качеству без RBAC-роли."""
    from app.models.agent import Agent

    if user.is_superuser or not is_quality_deputy_position(user.position):
        return agents

    agent = await db.scalar(select(Agent).where(Agent.slug == ND_CONTROL_AGENT_SLUG))
    if agent is None:
        return agents

    known_ids = {item.id for item in agents}
    if agent.id in known_ids:
        return agents

    return sorted([*agents, agent], key=lambda item: item.name)
