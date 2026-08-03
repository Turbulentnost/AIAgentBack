"""Add meeting categories and role positions to scheduled_meetings."""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.services.meeting_schedule_categories import MEETING_CATEGORY_NAMES


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


async def _column_exists(db, table_name: str, column_name: str) -> bool:
    result = await db.execute(
        text(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = :table_name
              AND column_name = :column_name
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    )
    return bool(result.scalar())


async def main() -> None:
    async with AsyncSessionLocal() as db:
        if not await _table_exists(db, "meeting_categories"):
            await db.execute(
                text(
                    """
                    CREATE TABLE meeting_categories (
                        id UUID PRIMARY KEY,
                        name VARCHAR(256) NOT NULL,
                        sort_order INTEGER NOT NULL DEFAULT 0,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        CONSTRAINT uq_meeting_categories_name UNIQUE (name)
                    )
                    """
                )
            )
            await db.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS ix_meeting_categories_name
                    ON meeting_categories (name)
                    """
                )
            )
            await db.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS ix_meeting_categories_sort_order
                    ON meeting_categories (sort_order)
                    """
                )
            )

        for index, name in enumerate(MEETING_CATEGORY_NAMES, start=1):
            await db.execute(
                text(
                    """
                    INSERT INTO meeting_categories (id, name, sort_order, is_active)
                    VALUES (:id, :name, :sort_order, TRUE)
                    ON CONFLICT (name) DO UPDATE
                    SET sort_order = EXCLUDED.sort_order,
                        is_active = TRUE
                    """
                ),
                {"id": str(uuid.uuid4()), "name": name, "sort_order": index},
            )

        default_category = await db.execute(
            text(
                """
                SELECT id FROM meeting_categories
                WHERE name = :name
                LIMIT 1
                """
            ),
            {"name": MEETING_CATEGORY_NAMES[0]},
        )
        default_category_id = default_category.scalar()
        if default_category_id is None:
            raise RuntimeError("Не удалось создать справочник видов совещаний")

        fallback_position = await db.execute(
            text(
                """
                SELECT id FROM positions
                WHERE is_active = TRUE
                ORDER BY name ASC
                LIMIT 1
                """
            )
        )
        fallback_position_id = fallback_position.scalar()

        for column_name in (
            "meeting_category_id",
            "manager_position_id",
            "responsible_position_id",
        ):
            if await _column_exists(db, "scheduled_meetings", column_name):
                continue

            if column_name == "meeting_category_id":
                await db.execute(
                    text(
                        """
                        ALTER TABLE scheduled_meetings
                        ADD COLUMN meeting_category_id UUID
                        """
                    )
                )
                await db.execute(
                    text(
                        """
                        UPDATE scheduled_meetings
                        SET meeting_category_id = :category_id
                        WHERE meeting_category_id IS NULL
                        """
                    ),
                    {"category_id": str(default_category_id)},
                )
                await db.execute(
                    text(
                        """
                        ALTER TABLE scheduled_meetings
                        ALTER COLUMN meeting_category_id SET NOT NULL
                        """
                    )
                )
                await db.execute(
                    text(
                        """
                        ALTER TABLE scheduled_meetings
                        ADD CONSTRAINT fk_scheduled_meetings_meeting_category_id_meeting_categories
                        FOREIGN KEY (meeting_category_id)
                        REFERENCES meeting_categories(id)
                        ON DELETE RESTRICT
                        """
                    )
                )
                await db.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS ix_scheduled_meetings_meeting_category_id
                        ON scheduled_meetings (meeting_category_id)
                        """
                    )
                )
                continue

            if fallback_position_id is None:
                raise RuntimeError(
                    "Нет активных должностей для backfill manager/responsible; "
                    "сначала синхронизируйте справочник positions"
                )

            await db.execute(
                text(
                    f"""
                    ALTER TABLE scheduled_meetings
                    ADD COLUMN {column_name} UUID
                    """
                )
            )
            await db.execute(
                text(
                    f"""
                    UPDATE scheduled_meetings
                    SET {column_name} = :position_id
                    WHERE {column_name} IS NULL
                    """
                ),
                {"position_id": str(fallback_position_id)},
            )
            await db.execute(
                text(
                    f"""
                    ALTER TABLE scheduled_meetings
                    ALTER COLUMN {column_name} SET NOT NULL
                    """
                )
            )
            await db.execute(
                text(
                    f"""
                    ALTER TABLE scheduled_meetings
                    ADD CONSTRAINT fk_scheduled_meetings_{column_name}_positions
                    FOREIGN KEY ({column_name})
                    REFERENCES positions(id)
                    ON DELETE RESTRICT
                    """
                )
            )
            await db.execute(
                text(
                    f"""
                    CREATE INDEX IF NOT EXISTS ix_scheduled_meetings_{column_name}
                    ON scheduled_meetings ({column_name})
                    """
                )
            )

        await db.commit()
        print("scheduled_meeting categories and roles migration applied")


if __name__ == "__main__":
    asyncio.run(main())
