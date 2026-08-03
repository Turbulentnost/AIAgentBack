"""Create meeting_registry_events table and cancelled_at column if missing."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime

from sqlalchemy import text

from app.db.session import AsyncSessionLocal


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


async def main() -> None:
    async with AsyncSessionLocal() as db:
        enum_exists = await db.execute(
            text("SELECT 1 FROM pg_type WHERE typname = 'meetingregistryeventtype'")
        )
        if not enum_exists.scalar():
            await db.execute(
                text(
                    """
                    CREATE TYPE meetingregistryeventtype AS ENUM (
                        'invitations_sent',
                        'rescheduled',
                        'cancelled',
                        'participants_updated',
                        'stage_changed'
                    )
                    """
                )
            )

        cancelled_col = await db.execute(
            text(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'meeting_registry_entries'
                  AND column_name = 'cancelled_at'
                """
            )
        )
        if not cancelled_col.scalar():
            await db.execute(
                text(
                    """
                    ALTER TABLE meeting_registry_entries
                    ADD COLUMN cancelled_at TIMESTAMPTZ
                    """
                )
            )
            await db.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS ix_meeting_registry_entries_cancelled_at
                    ON meeting_registry_entries (cancelled_at)
                    """
                )
            )

        events_table = await db.execute(
            text(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'meeting_registry_events'
                """
            )
        )
        if not events_table.scalar():
            await db.execute(
                text(
                    """
                    CREATE TABLE meeting_registry_events (
                        id UUID PRIMARY KEY,
                        registry_entry_id UUID NOT NULL
                            REFERENCES meeting_registry_entries(id) ON DELETE CASCADE,
                        memo_ref_key VARCHAR(36) NOT NULL,
                        occurred_at TIMESTAMPTZ NOT NULL,
                        event_type meetingregistryeventtype NOT NULL,
                        message TEXT NOT NULL,
                        actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
                        payload JSONB
                    )
                    """
                )
            )
            await db.execute(
                text(
                    "CREATE INDEX ix_meeting_registry_events_registry_entry_id "
                    "ON meeting_registry_events (registry_entry_id)"
                )
            )
            await db.execute(
                text(
                    "CREATE INDEX ix_meeting_registry_events_memo_ref_key "
                    "ON meeting_registry_events (memo_ref_key)"
                )
            )
            await db.execute(
                text(
                    "CREATE INDEX ix_meeting_registry_events_occurred_at "
                    "ON meeting_registry_events (occurred_at)"
                )
            )

        rows = (
            await db.execute(
                text(
                    """
                    SELECT id, memo_ref_key, invitations_sent_at, payload
                    FROM meeting_registry_entries
                    """
                )
            )
        ).mappings().all()

        backfilled = 0
        for row in rows:
            payload = row["payload"] if isinstance(row["payload"], dict) else {}
            entry_id = row["id"]
            memo_ref_key = row["memo_ref_key"]
            invitations_sent_at = row["invitations_sent_at"]

            cancelled_at = _parse_iso(payload.get("cancelled_at"))
            if cancelled_at is not None:
                await db.execute(
                    text(
                        """
                        UPDATE meeting_registry_entries
                        SET cancelled_at = :cancelled_at
                        WHERE id = :id AND cancelled_at IS NULL
                        """
                    ),
                    {"cancelled_at": cancelled_at, "id": entry_id},
                )

            existing = await db.execute(
                text(
                    "SELECT 1 FROM meeting_registry_events WHERE registry_entry_id = :id LIMIT 1"
                ),
                {"id": entry_id},
            )
            if existing.scalar():
                continue

            events: list[dict[str, object]] = []
            if invitations_sent_at is not None:
                events.append(
                    {
                        "event_type": "invitations_sent",
                        "occurred_at": invitations_sent_at,
                        "message": "Отправлены приглашения",
                        "payload": {"attendees": payload.get("attendees") or []},
                    }
                )
            rescheduled_at = _parse_iso(payload.get("rescheduled_at"))
            if rescheduled_at is not None:
                events.append(
                    {
                        "event_type": "rescheduled",
                        "occurred_at": rescheduled_at,
                        "message": str(payload.get("reschedule_message") or "Совещание перенесено"),
                        "payload": {
                            "rescheduled_by_user_id": payload.get("rescheduled_by_user_id"),
                        },
                    }
                )
            if cancelled_at is not None:
                events.append(
                    {
                        "event_type": "cancelled",
                        "occurred_at": cancelled_at,
                        "message": str(payload.get("cancel_message") or "Совещание отменено"),
                        "payload": {
                            "cancelled_by_user_id": payload.get("cancelled_by_user_id"),
                            "outlook_cancelled": payload.get("outlook_cancelled"),
                        },
                    }
                )
            participants_updated_at = _parse_iso(payload.get("participants_updated_at"))
            if participants_updated_at is not None:
                events.append(
                    {
                        "event_type": "participants_updated",
                        "occurred_at": participants_updated_at,
                        "message": str(
                            payload.get("participants_update_message")
                            or "Состав участников изменён"
                        ),
                        "payload": {
                            "participants_updated_by_user_id": payload.get(
                                "participants_updated_by_user_id"
                            ),
                        },
                    }
                )

            for event in events:
                actor_id = None
                if event["event_type"] == "rescheduled":
                    raw_actor = payload.get("rescheduled_by_user_id")
                elif event["event_type"] == "cancelled":
                    raw_actor = payload.get("cancelled_by_user_id")
                elif event["event_type"] == "participants_updated":
                    raw_actor = payload.get("participants_updated_by_user_id")
                else:
                    raw_actor = None
                if isinstance(raw_actor, str) and raw_actor.strip():
                    try:
                        actor_id = uuid.UUID(raw_actor.strip())
                    except ValueError:
                        actor_id = None

                await db.execute(
                    text(
                        """
                        INSERT INTO meeting_registry_events (
                            id, registry_entry_id, memo_ref_key, occurred_at,
                            event_type, message, actor_user_id, payload
                        ) VALUES (
                            :id, :registry_entry_id, :memo_ref_key, :occurred_at,
                            :event_type, :message, :actor_user_id, CAST(:payload AS jsonb)
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "registry_entry_id": entry_id,
                        "memo_ref_key": memo_ref_key,
                        "occurred_at": event["occurred_at"],
                        "event_type": event["event_type"],
                        "message": event["message"],
                        "actor_user_id": actor_id,
                        "payload": json.dumps(event["payload"], ensure_ascii=False),
                    },
                )
            backfilled += 1

        await db.commit()
        print(f"events_table_ready backfilled_entries={backfilled}")


if __name__ == "__main__":
    asyncio.run(main())
