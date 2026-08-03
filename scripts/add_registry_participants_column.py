"""Add participants JSONB column to meeting_registry_entries if missing."""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.db.session import AsyncSessionLocal


async def main() -> None:
    async with AsyncSessionLocal() as db:
        exists = await db.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'meeting_registry_entries'
                  AND column_name = 'participants'
                """
            )
        )
        if exists.scalar():
            print("column already exists")
            return
        await db.execute(
            text(
                """
                ALTER TABLE meeting_registry_entries
                ADD COLUMN participants JSONB NOT NULL DEFAULT '[]'::jsonb
                """
            )
        )
        await db.commit()
        print("column added")


if __name__ == "__main__":
    asyncio.run(main())
