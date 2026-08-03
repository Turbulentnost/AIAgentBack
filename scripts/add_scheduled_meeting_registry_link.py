"""Link meeting_registry_entries to scheduled_meetings (one card per series)."""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.db.session import AsyncSessionLocal


async def main() -> None:
    async with AsyncSessionLocal() as db:
        stage_enum = await db.execute(
            text(
                """
                SELECT 1 FROM pg_enum
                JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
                WHERE pg_type.typname = 'meetingregistrystage'
                  AND pg_enum.enumlabel = 'scheduled'
                """
            )
        )
        if not stage_enum.scalar():
            await db.execute(
                text("ALTER TYPE meetingregistrystage ADD VALUE IF NOT EXISTS 'scheduled'")
            )

        event_enum = await db.execute(
            text(
                """
                SELECT 1 FROM pg_enum
                JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
                WHERE pg_type.typname = 'meetingregistryeventtype'
                  AND pg_enum.enumlabel = 'occurrence_rolled'
                """
            )
        )
        if not event_enum.scalar():
            await db.execute(
                text(
                    "ALTER TYPE meetingregistryeventtype "
                    "ADD VALUE IF NOT EXISTS 'occurrence_rolled'"
                )
            )

        scheduled_col = await db.execute(
            text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'meeting_registry_entries'
                  AND column_name = 'scheduled_meeting_id'
                """
            )
        )
        if not scheduled_col.scalar():
            await db.execute(
                text(
                    """
                    ALTER TABLE meeting_registry_entries
                    ADD COLUMN scheduled_meeting_id UUID
                        REFERENCES scheduled_meetings(id) ON DELETE SET NULL
                    """
                )
            )
            await db.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS
                    ix_meeting_registry_entries_scheduled_meeting_id
                    ON meeting_registry_entries (scheduled_meeting_id)
                    """
                )
            )
            await db.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS
                    uq_meeting_registry_entries_scheduled_meeting_id
                    ON meeting_registry_entries (scheduled_meeting_id)
                    WHERE scheduled_meeting_id IS NOT NULL
                    """
                )
            )

        occurrence_col = await db.execute(
            text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'meeting_registry_entries'
                  AND column_name = 'series_occurrence_date'
                """
            )
        )
        if not occurrence_col.scalar():
            await db.execute(
                text(
                    """
                    ALTER TABLE meeting_registry_entries
                    ADD COLUMN series_occurrence_date DATE
                    """
                )
            )

        await db.commit()
        print("scheduled_meeting registry link migration applied")


if __name__ == "__main__":
    asyncio.run(main())
