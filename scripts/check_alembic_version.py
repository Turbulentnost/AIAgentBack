import asyncio
import sys

from sqlalchemy import text

from app.db.session import AsyncSessionLocal

TARGET_REVISION = "0047_meeting_registry_events"


async def main() -> None:
    async with AsyncSessionLocal() as session:
        versions = (
            await session.execute(text("SELECT version_num FROM alembic_version"))
        ).scalars().all()
        print("alembic_version:", versions)
        for table in (
            "scheduled_meetings",
            "meeting_registry_events",
            "meeting_registry_entries",
        ):
            exists = (
                await session.execute(
                    text("SELECT to_regclass(:name)"),
                    {"name": f"public.{table}"},
                )
            ).scalar()
            print(f"{table}:", exists)

        if "--fix-stamp" in sys.argv and versions:
            current = versions[0]
            if current != TARGET_REVISION:
                await session.execute(
                    text("UPDATE alembic_version SET version_num = :target"),
                    {"target": TARGET_REVISION},
                )
                await session.commit()
                print(f"updated alembic_version: {current} -> {TARGET_REVISION}")

        if "--details" in sys.argv:
            cols = (
                await session.execute(
                    text(
                        """
                        SELECT table_name, column_name, data_type
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name IN (
                            'scheduled_meetings',
                            'scheduled_meeting_participants'
                          )
                        ORDER BY table_name, ordinal_position
                        """
                    )
                )
            ).all()
            print("columns:")
            for table_name, column_name, data_type in cols:
                print(f"  {table_name}.{column_name} ({data_type})")
            meetings_count = (
                await session.execute(text("SELECT COUNT(*) FROM scheduled_meetings"))
            ).scalar()
            participants_count = (
                await session.execute(
                    text("SELECT COUNT(*) FROM scheduled_meeting_participants")
                )
            ).scalar()
            print("scheduled_meetings rows:", meetings_count)
            print("scheduled_meeting_participants rows:", participants_count)


if __name__ == "__main__":
    asyncio.run(main())
