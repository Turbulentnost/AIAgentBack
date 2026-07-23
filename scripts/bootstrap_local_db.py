"""Создаёт локальную БД ai_agents и тестового пользователя для dev."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.models import *  # noqa: F401,F403
from app.models.user import User
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_EMAIL = "temp.nd@local.dev"
DEFAULT_PASSWORD = "NdTemp2026!"


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


async def ensure_schema_and_user() -> None:
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Schema ensured via metadata.create_all")

    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        from sqlalchemy import select

        existing = await session.scalar(select(User).where(User.email == DEFAULT_EMAIL))
        if existing is None:
            session.add(
                User(
                    email=DEFAULT_EMAIL,
                    full_name="Temp ND",
                    hashed_password=hash_password(DEFAULT_PASSWORD),
                    is_active=True,
                    is_verified=True,
                    is_superuser=True,
                    must_change_password=False,
                )
            )
            await session.commit()
            print(f"Created dev user {DEFAULT_EMAIL}")
        else:
            print(f"Dev user {DEFAULT_EMAIL} already exists")
    await engine.dispose()


async def main() -> None:
    print(
        f"Connecting to postgres://{settings.POSTGRES_USER}@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    )
    await ensure_database()
    await ensure_schema_and_user()
    print("Local bootstrap complete")


if __name__ == "__main__":
    asyncio.run(main())
