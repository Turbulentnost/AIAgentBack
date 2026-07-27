"""Create app_notifications table for in-app bell notifications."""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.db.session import AsyncSessionLocal


async def _table_exists(db, table_name: str) -> bool:
    result = await db.execute(
        text(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_name = :table_name
            """
        ),
        {"table_name": table_name},
    )
    return bool(result.scalar())


async def main() -> None:
    async with AsyncSessionLocal() as db:
        if await _table_exists(db, "app_notifications"):
            print("app_notifications already exists")
            return

        await db.execute(
            text(
                """
                CREATE TABLE app_notifications (
                    id UUID PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    type VARCHAR(64) NOT NULL,
                    title VARCHAR(512) NOT NULL,
                    body TEXT NOT NULL,
                    entity_key VARCHAR(255) NOT NULL,
                    payload JSONB,
                    read_at TIMESTAMPTZ,
                    opened_at TIMESTAMPTZ,
                    resolved_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    CONSTRAINT uq_app_notifications_user_id_entity_key
                        UNIQUE (user_id, entity_key)
                )
                """
            )
        )
        await db.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_app_notifications_user_id
                ON app_notifications (user_id)
                """
            )
        )
        await db.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_app_notifications_type
                ON app_notifications (type)
                """
            )
        )
        await db.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_app_notifications_entity_key
                ON app_notifications (entity_key)
                """
            )
        )
        await db.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_app_notifications_resolved_at
                ON app_notifications (resolved_at)
                """
            )
        )
        await db.commit()
        print("app_notifications created")


if __name__ == "__main__":
    asyncio.run(main())
