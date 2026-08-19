"""Bootstrap схемы БД и пользователей для desktop sidecar."""
from __future__ import annotations

import asyncpg

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine
from app.services.aveon_desktop_users import ensure_aveon_desktop_users

logger = get_logger(__name__)


async def ensure_desktop_database_exists() -> None:
    """Создаёт БД ai_agents, если её ещё нет на локальном Postgres."""
    conn = await asyncpg.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        database="postgres",
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            settings.POSTGRES_DB,
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{settings.POSTGRES_DB}"')
            logger.info("app_desktop.database_created", database=settings.POSTGRES_DB)
    finally:
        await conn.close()


async def ensure_desktop_schema() -> None:
    """Гарантирует наличие таблиц users/sessions и пр. на чистой установке."""
    # Импорт моделей регистрирует metadata.
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("app_desktop.schema_ensured")


async def bootstrap_desktop_auth_store() -> list[str]:
    """Полный bootstrap: БД → схема → пользователи @local.dev с актуальными паролями."""
    await ensure_desktop_database_exists()
    await ensure_desktop_schema()
    async with AsyncSessionLocal() as session:
        touched = await ensure_aveon_desktop_users(session)
    logger.info(
        "app_desktop.users_ensured",
        count=len(touched),
        host=settings.POSTGRES_HOST,
        database=settings.POSTGRES_DB,
    )
    return touched


__all__ = [
    "bootstrap_desktop_auth_store",
    "ensure_desktop_database_exists",
    "ensure_desktop_schema",
]
