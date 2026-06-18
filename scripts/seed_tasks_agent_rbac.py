"""Seed tasks_agent RBAC: agent record, permission, access for all users."""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.agents.tasks_agent.config import AGENT_ID, AGENT_NAME, AGENT_PURPOSE, AGENT_VERSION
from app.db.session import AsyncSessionLocal
from app.models.agent import Agent
from app.models.enums import AgentStatus
from app.models.user import Department, DepartmentAgent, Permission, Role, role_permissions
from app.services.tasks_permission import EMPLOYEE_ROLE_CODE, TASKS_RUN_PERMISSION


async def _ensure_role_permission(db, role: Role, permission: Permission) -> bool:
    existing = await db.execute(
        select(role_permissions.c.role_id).where(
            role_permissions.c.role_id == role.id,
            role_permissions.c.permission_id == permission.id,
        )
    )
    if existing.first() is not None:
        return False
    await db.execute(
        role_permissions.insert().values(
            role_id=role.id,
            permission_id=permission.id,
        )
    )
    return True


async def seed_tasks_agent_rbac() -> None:
    async with AsyncSessionLocal() as db:
        agent = await db.scalar(select(Agent).where(Agent.slug == AGENT_ID))
        if agent is None:
            agent = Agent(
                name=AGENT_NAME,
                slug=AGENT_ID,
                purpose=AGENT_PURPOSE,
                status=AgentStatus.ACTIVE,
            )
            db.add(agent)
            await db.flush()
            print(f"Created agent: {AGENT_ID}")
        else:
            agent.name = AGENT_NAME
            agent.purpose = AGENT_PURPOSE
            if agent.status != AgentStatus.ACTIVE:
                agent.status = AgentStatus.ACTIVE
            print(f"Updated agent: {AGENT_ID}")

        permission = await db.scalar(select(Permission).where(Permission.code == TASKS_RUN_PERMISSION))
        if permission is None:
            permission = Permission(
                code=TASKS_RUN_PERMISSION,
                name="Запуск агента поручений",
                description="Разрешает доступ к tasks_agent и ежедневной сводке поручений",
            )
            db.add(permission)
            await db.flush()
            print(f"Created permission: {TASKS_RUN_PERMISSION}")
        else:
            print(f"Permission exists: {TASKS_RUN_PERMISSION}")

        for role_code in ("admin", EMPLOYEE_ROLE_CODE):
            role = await db.scalar(select(Role).where(Role.code == role_code))
            if role is None:
                print(f"Role not found, skipped: {role_code}")
                continue
            if await _ensure_role_permission(db, role, permission):
                print(f"Linked permission to role: {role_code}")

        departments = list((await db.execute(select(Department).where(Department.is_active.is_(True)))).scalars().all())
        linked = 0
        for department in departments:
            grant = await db.scalar(
                select(DepartmentAgent).where(
                    DepartmentAgent.department_id == department.id,
                    DepartmentAgent.agent_id == agent.id,
                )
            )
            if grant is None:
                db.add(
                    DepartmentAgent(
                        department_id=department.id,
                        agent_id=agent.id,
                        access_level="run",
                        can_run=True,
                        can_view_results=True,
                    )
                )
                linked += 1
        print(f"Department grants added: {linked} (total departments: {len(departments)})")

        await db.commit()
        print(f"Seed complete (agent version {AGENT_VERSION})")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(seed_tasks_agent_rbac())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
