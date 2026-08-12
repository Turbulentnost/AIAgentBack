"""Создаёт локальную БД ai_agents и тестовых пользователей для dev."""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.models import *  # noqa: F401,F403
from app.services.document_analysis_permission import ensure_avion_only_user_agent_grant
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


@dataclass(frozen=True)
class DevUserSpec:
    email: str
    password: str
    full_name: str
    first_name: str
    last_name: str
    is_superuser: bool = False


DEV_USERS: tuple[DevUserSpec, ...] = (
    DevUserSpec(
        email="temp.nd@local.dev",
        password="NdTemp2026!",
        full_name="Temp ND",
        first_name="Temp",
        last_name="ND",
        is_superuser=True,
    ),
    DevUserSpec(
        email="rodionov.pavel@local.dev",
        password="Rodionov2026!",
        full_name="Родионов Павел",
        first_name="Павел",
        last_name="Родионов",
    ),
    DevUserSpec(
        email="tishchenko.nadezhda@local.dev",
        password="Tishchenko2026!",
        full_name="Тищенко Надежда",
        first_name="Надежда",
        last_name="Тищенко",
    ),
    DevUserSpec(
        email="aksinin.leonid@local.dev",
        password="Aksinin2026!",
        full_name="Аксинин Леонид",
        first_name="Леонид",
        last_name="Аксинин",
    ),
)


async def ensure_database() -> None:
    conn = await asyncpg.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        database="postgres",
    )
    exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", settings.POSTGRES_DB)
    if not exists:
        await conn.execute(f'CREATE DATABASE "{settings.POSTGRES_DB}"')
        print(f"Created database {settings.POSTGRES_DB}")
    else:
        print(f"Database {settings.POSTGRES_DB} already exists")
    await conn.close()


async def ensure_schema_and_users() -> None:
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Schema ensured via metadata.create_all")

    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        from sqlalchemy import select

        for spec in DEV_USERS:
            user = await session.scalar(select(User).where(User.email == spec.email))
            if user is None:
                user = User(
                    email=spec.email,
                    full_name=spec.full_name,
                    first_name=spec.first_name,
                    last_name=spec.last_name,
                    hashed_password=hash_password(spec.password),
                    is_active=True,
                    is_verified=True,
                    is_superuser=spec.is_superuser,
                    must_change_password=False,
                )
                session.add(user)
                await session.flush()
                print(f"Created dev user {spec.email}")
            else:
                print(f"Dev user {spec.email} already exists")
            if await ensure_avion_only_user_agent_grant(session, user):
                print(f"Granted Avion-only access for {spec.email}")
        await session.commit()
    await engine.dispose()


async def main() -> None:
    print(
        f"Connecting to postgres://{settings.POSTGRES_USER}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    )
    await ensure_database()
    await ensure_schema_and_users()
    print("Local bootstrap complete")
    print("Leader account (coverage dashboard, all tasks):")
    print("  - Родионов Павел: rodionov.pavel@local.dev / Rodionov2026!")
    print("Manager accounts (personal tasks tab):")
    for spec in DEV_USERS:
        if spec.is_superuser or spec.email == "rodionov.pavel@local.dev":
            continue
        print(f"  - {spec.full_name}: {spec.email} / {spec.password}")


if __name__ == "__main__":
    asyncio.run(main())
