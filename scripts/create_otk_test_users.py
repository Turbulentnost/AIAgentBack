"""Создать тестовых пользователей ОТК с доступом к ESKD Agent.

Пример:
  cd AIAgentBack && python scripts/create_otk_test_users.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PASSWORD = "Test123!"

OTK_USERS = (
    {
        "login": "otk.timofeev",
        "email": "otk.timofeev@example.com",
        "full_name": "Тимофеев Максим Юрьевич",
        "last_name": "Тимофеев",
        "first_name": "Максим",
        "middle_name": "Юрьевич",
    },
    {
        "login": "otk.ermakov",
        "email": "otk.ermakov@example.com",
        "full_name": "Ермаков Илья Николаевич",
        "last_name": "Ермаков",
        "first_name": "Илья",
        "middle_name": "Николаевич",
    },
    {
        "login": "otk.voroshina",
        "email": "otk.voroshina@example.com",
        "full_name": "Ворошина Елена Владимировна",
        "last_name": "Ворошина",
        "first_name": "Елена",
        "middle_name": "Владимировна",
    },
    {
        "login": "otk.babalykhyan",
        "email": "otk.babalykhyan@example.com",
        "full_name": "Бабалыхян Арсен Арсенович",
        "last_name": "Бабалыхян",
        "first_name": "Арсен",
        "middle_name": "Арсенович",
    },
    {
        "login": "otk.sarkisyan",
        "email": "otk.sarkisyan@example.com",
        "full_name": "Саркисян Андрей Александрович",
        "last_name": "Саркисян",
        "first_name": "Андрей",
        "middle_name": "Александрович",
    },
    {
        "login": "otk.gruntovskiy",
        "email": "otk.gruntovskiy@example.com",
        "full_name": "Грунтовский Дмитрий Дмитриевич",
        "last_name": "Грунтовский",
        "first_name": "Дмитрий",
        "middle_name": "Дмитриевич",
    },
    {
        "login": "otk.arsunov",
        "email": "otk.arsunov@example.com",
        "full_name": "Арсуноев Михаил Магомедович",
        "last_name": "Арсуноев",
        "first_name": "Михаил",
        "middle_name": "Магомедович",
    },
)


async def _upsert_user(session, service, row, *, role, department, agent) -> str:
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.models.user import User, UserAgent
    from app.schemas.user import UserCreate

    existing = await service.get_by_email(row["email"])
    if existing is not None:
        existing.hashed_password = hash_password(PASSWORD)
        existing.username = row["login"]
        existing.full_name = row["full_name"]
        existing.last_name = row["last_name"]
        existing.first_name = row["first_name"]
        existing.middle_name = row["middle_name"]
        existing.position = "Сотрудник ОТК"
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
                email=row["email"],
                username=row["login"],
                password=PASSWORD,
                full_name=row["full_name"],
                last_name=row["last_name"],
                first_name=row["first_name"],
                middle_name=row["middle_name"],
                position="Сотрудник ОТК",
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

    return action


async def main() -> int:
    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal
    from app.models.agent import Agent
    from app.models.user import Department, Role
    from app.services.user_service import UserService

    async with AsyncSessionLocal() as session:
        role = await session.scalar(select(Role).where(Role.code == "employee"))
        department = await session.scalar(
            select(Department).where(Department.slug.in_(("отк-1", "отк-2", "otk"))).limit(1)
        )
        agent = await session.scalar(select(Agent).where(Agent.slug == "eskd_agent"))
        service = UserService(session)

        print(f"password={PASSWORD}")
        print(f"department={department.name if department else None}")
        print(f"agent={agent.slug if agent else None}")
        print("---")

        for row in OTK_USERS:
            action = await _upsert_user(
                session,
                service,
                row,
                role=role,
                department=department,
                agent=agent,
            )
            print(f"{action}\t{row['login']}\t{row['email']}\t{row['full_name']}")

        await session.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
