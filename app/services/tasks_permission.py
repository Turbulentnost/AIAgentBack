from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.user import Role, User
from app.services.permission_service import PermissionService

TASKS_AGENT_SLUG = "tasks_agent"
TASKS_RUN_PERMISSION = "agents.tasks_agent.run"
EMPLOYEE_ROLE_CODE = "employee"


async def user_has_admin_role(db: AsyncSession, user: User) -> bool:
    if user.role_id is not None:
        primary_code = await db.scalar(select(Role.code).where(Role.id == user.role_id))
        if primary_code == "admin":
            return True
    from app.models.user import Role as RoleModel, user_roles

    result = await db.execute(
        select(RoleModel.code)
        .join(user_roles, user_roles.c.role_id == RoleModel.id)
        .where(user_roles.c.user_id == user.id)
    )
    return "admin" in {row[0] for row in result.all()}


async def can_access_tasks_agent(db: AsyncSession, user: User) -> bool:
    if user.is_superuser:
        return True
    if await user_has_admin_role(db, user):
        return True

    agent = await db.scalar(select(Agent).where(Agent.slug == TASKS_AGENT_SLUG))
    if agent is None:
        return False
    return await PermissionService(db).can_access_agent(user, agent.id, action="run")
