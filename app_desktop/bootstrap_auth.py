"""Bootstrap встроенной SQLite БД и пользователей для desktop (без PostgreSQL)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine
from app.services.aveon_desktop_users import ensure_aveon_desktop_users

logger = get_logger(__name__)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kw):  # noqa: ANN001
    return "JSON"


@compiles(TSVECTOR, "sqlite")
def _compile_tsvector_sqlite(_type, _compiler, **_kw):  # noqa: ANN001
    return "TEXT"


async def ensure_desktop_schema() -> None:
    """Создаёт таблицы в SQLite рядом с приложением."""
    import app.models  # noqa: F401

    sqlite_path = (settings.DESKTOP_SQLITE_PATH or "").strip()
    if sqlite_path:
        Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("app_desktop.schema_ensured", sqlite=bool(sqlite_path))


async def bootstrap_desktop_auth_store() -> list[str]:
    """Installer-only: SQLite файл + seed @local.dev пользователей."""
    await ensure_desktop_schema()
    async with AsyncSessionLocal() as session:
        touched = await ensure_aveon_desktop_users(session)
    logger.info(
        "app_desktop.users_ensured",
        count=len(touched),
        database=settings.DESKTOP_SQLITE_PATH or settings.POSTGRES_DB,
    )
    return touched


async def bootstrap_desktop_catalog() -> dict:
    """Installer-only: спецификации/материалы из встроенного snapshot (без Postgres/1С)."""
    from app_desktop.bootstrap_specs import ensure_desktop_resource_specs

    return await ensure_desktop_resource_specs()


__all__ = [
    "bootstrap_desktop_auth_store",
    "bootstrap_desktop_catalog",
    "ensure_desktop_schema",
]
