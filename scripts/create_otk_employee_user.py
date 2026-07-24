"""Создать тестового пользователя с должностью «Сотрудник ОТК».

Пример:
  python scripts/create_otk_employee_user.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EMAIL = "otk.employee@example.com"
USERNAME = "otk_employee"
PASSWORD = "OtkTemp2026!"
FULL_NAME = "Сотрудник ОТК (тест)"
POSITION = "Сотрудник ОТК"
LAST_NAME = "ОТК"
FIRST_NAME = "Сотрудник"


async def main() -> int:
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.db.session import AsyncSessionLocal
    from app.models.agent import Agent
    from app.models.user import Department, Role, User, UserAgent
    from app.schemas.user import UserCreate
    from app.services.user_service import UserService

    async with AsyncSessionLocal() as session:
        role = await session.scalar(select(Role).where(Role.code == "employee"))
        department = await session.scalar(
            select(Department).where(Department.slug.in_(("отк-1", "отк-2"))).limit(1)
        )
        agent = await session.scalar(
            select(Agent).where(Agent.slug == "quality_engineer_agent")
        )
        service = UserService(session)
        existing = await service.get_by_email(EMAIL)
        if existing is not None:
            existing.hashed_password = hash_password(PASSWORD)
            existing.username = USERNAME
            existing.full_name = FULL_NAME
            existing.last_name = LAST_NAME
            existing.first_name = FIRST_NAME
            existing.position = POSITION
            existing.is_active = True
            existing.is_superuser = False
            existing.is_verified = True
            existing.must_change_password = False
            if role is not None:
                existing.role_id = role.id
            if department is not None:
                existing.department_id = department.id
            user = existing
            action = "UPDATED"
        else:
            user = await service.create(
                UserCreate(
                    email=EMAIL,
                    username=USERNAME,
                    password=PASSWORD,
                    full_name=FULL_NAME,
                    last_name=LAST_NAME,
                    first_name=FIRST_NAME,
                    position=POSITION,
                    department_id=department.id if department else None,
                    role_id=role.id if role else None,
                ),
                is_superuser=False,
            )
            user.is_verified = True
            user.must_change_password = False
            action = "CREATED"

        if agent is not None:
            grant = await session.scalar(
                select(UserAgent).where(
                    UserAgent.user_id == user.id,
                    UserAgent.agent_id == agent.id,
                )
            )
            if grant is None:
                session.add(
                    UserAgent(
                        user_id=user.id,
                        agent_id=agent.id,
                        access_level="run",
                        can_run=True,
                        can_view_results=True,
                        can_approve=False,
                        can_configure=False,
                    )
                )

        await session.commit()
        print(action)
        print(f"email={EMAIL}")
        print(f"username={USERNAME}")
        print(f"password={PASSWORD}")
        print(f"position={POSITION}")
        print(f"user_id={user.id}")
        print(f"department={department.name if department else None}")
        print(f"agent={agent.slug if agent else None}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
