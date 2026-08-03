from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    from sqlalchemy import text

    from app.db.base import Base
    from app.models import check_run, check_run_change, integration, marking, user  # noqa: F401

    _schema_patches = (
        "ALTER TABLE eskd_check_runs ADD COLUMN IF NOT EXISTS human_verified_at TIMESTAMPTZ",
        "ALTER TABLE eskd_check_runs ADD COLUMN IF NOT EXISTS document_key VARCHAR(128)",
        "ALTER TABLE eskd_check_runs ADD COLUMN IF NOT EXISTS version_no INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE eskd_check_runs ADD COLUMN IF NOT EXISTS parent_run_id UUID",
        "ALTER TABLE eskd_check_runs ADD COLUMN IF NOT EXISTS created_by_user_id UUID",
        "ALTER TABLE eskd_check_runs ADD COLUMN IF NOT EXISTS created_by_login VARCHAR(64)",
        "ALTER TABLE eskd_check_runs ADD COLUMN IF NOT EXISTS created_by_name VARCHAR(256)",
        "ALTER TABLE eskd_check_runs ADD COLUMN IF NOT EXISTS verified_by_user_id UUID",
        "ALTER TABLE eskd_check_runs ADD COLUMN IF NOT EXISTS verified_by_login VARCHAR(64)",
        "ALTER TABLE eskd_check_runs ADD COLUMN IF NOT EXISTS verified_by_name VARCHAR(256)",
        "ALTER TABLE eskd_marking_labels ADD COLUMN IF NOT EXISTS human_verified_at TIMESTAMPTZ",
        "ALTER TABLE eskd_marking_labels ADD COLUMN IF NOT EXISTS verified_by_user_id UUID",
        "ALTER TABLE eskd_marking_labels ADD COLUMN IF NOT EXISTS verified_by_login VARCHAR(64)",
        "ALTER TABLE eskd_marking_labels ADD COLUMN IF NOT EXISTS verified_by_name VARCHAR(256)",
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in _schema_patches:
            await conn.execute(text(stmt))
