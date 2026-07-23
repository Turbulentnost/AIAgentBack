"""Add user fields to scheduled meetings and participants; backfill unambiguous rows."""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select, text

from app.db.session import AsyncSessionLocal
from app.models.scheduled_meeting import ScheduledMeeting, ScheduledMeetingParticipant
from app.models.user import User
from app.services.enterprise_positions_report import normalize_position_title
from app.utils.department_classification import normalize_position_name


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


async def _apply_schema(db) -> None:
    if not await _column_exists(db, "scheduled_meetings", "manager_user_id"):
        await db.execute(
            text(
                """
                ALTER TABLE scheduled_meetings
                ADD COLUMN manager_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL
                """
            )
        )
        await db.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_scheduled_meetings_manager_user_id
                ON scheduled_meetings (manager_user_id)
                """
            )
        )
    if not await _column_exists(db, "scheduled_meetings", "responsible_user_id"):
        await db.execute(
            text(
                """
                ALTER TABLE scheduled_meetings
                ADD COLUMN responsible_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL
                """
            )
        )
        await db.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_scheduled_meetings_responsible_user_id
                ON scheduled_meetings (responsible_user_id)
                """
            )
        )

    if not await _column_exists(db, "scheduled_meeting_participants", "user_id"):
        await db.execute(
            text(
                """
                ALTER TABLE scheduled_meeting_participants
                ADD COLUMN user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL
                """
            )
        )
        await db.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_scheduled_meeting_participants_user_id
                ON scheduled_meeting_participants (user_id)
                """
            )
        )
    if not await _column_exists(db, "scheduled_meeting_participants", "person_fio"):
        await db.execute(
            text(
                """
                ALTER TABLE scheduled_meeting_participants
                ADD COLUMN person_fio VARCHAR(255) NULL
                """
            )
        )
    if not await _column_exists(db, "scheduled_meeting_participants", "person_email"):
        await db.execute(
            text(
                """
                ALTER TABLE scheduled_meeting_participants
                ADD COLUMN person_email VARCHAR(255) NULL
                """
            )
        )

    await db.execute(
        text(
            """
            ALTER TABLE scheduled_meeting_participants
            ALTER COLUMN position_id DROP NOT NULL
            """
        )
    )

    await db.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_scheduled_meeting_participant_user
            ON scheduled_meeting_participants (scheduled_meeting_id, user_id)
            WHERE user_id IS NOT NULL
            """
        )
    )


def _users_by_position_title(users: list[User]) -> dict[str, list[User]]:
    grouped: dict[str, list[User]] = {}
    for user in users:
        position_key = normalize_position_title(
            normalize_position_name(user.position or "")
        )
        if not position_key:
            continue
        grouped.setdefault(position_key, []).append(user)
    return grouped


async def _backfill_participants(db) -> int:
    users_result = await db.execute(
        select(User).where(
            User.deleted_at.is_(None),
            User.is_active.is_(True),
            User.email.is_not(None),
        )
    )
    users = list(users_result.scalars().all())
    users_by_position = _users_by_position_title(users)

    participants_result = await db.execute(
        select(ScheduledMeetingParticipant).where(ScheduledMeetingParticipant.user_id.is_(None))
    )
    updated = 0
    from app.models.position import Position

    for participant in participants_result.scalars().all():
        if participant.position_id is None:
            continue
        position = await db.get(Position, participant.position_id)
        if position is None or not position.name:
            continue
        position_key = normalize_position_title(position.name)
        matches = users_by_position.get(position_key, [])
        if len(matches) != 1:
            continue
        user = matches[0]
        participant.user_id = user.id
        participant.person_fio = (user.full_name or "").strip() or None
        participant.person_email = (user.email or "").strip() or None
        updated += 1
    return updated


async def main() -> None:
    async with AsyncSessionLocal() as db:
        await _apply_schema(db)
        updated = await _backfill_participants(db)
        await db.commit()
        print(f"Backfilled participant users: {updated}")


if __name__ == "__main__":
    asyncio.run(main())
