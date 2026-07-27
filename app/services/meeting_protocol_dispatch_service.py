from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.enums import MeetingRegistryStage
from app.models.meeting_registry import MeetingRegistryEntry
from app.services.meeting_protocol_draft_service import MeetingProtocolDraftService

logger = get_logger(__name__)


@dataclass(frozen=True)
class ProtocolDraftDispatchResult:
    scheduled: int
    catchup_created: int
    skipped: int
    errors: list[str]


class MeetingProtocolDispatchService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.draft_service = MeetingProtocolDraftService(db)

    async def dispatch_due_entries(self) -> ProtocolDraftDispatchResult:
        if not settings.MEETING_PROTOCOL_DRAFT_ENABLED:
            return ProtocolDraftDispatchResult(
                scheduled=0,
                catchup_created=0,
                skipped=0,
                errors=["disabled"],
            )

        now = datetime.now(timezone.utc)
        lookahead = now + timedelta(hours=settings.MEETING_PROTOCOL_DISPATCH_LOOKAHEAD_HOURS)
        grace = now - timedelta(minutes=settings.MEETING_PROTOCOL_DISPATCH_CATCHUP_GRACE_MINUTES)

        scheduled = 0
        catchup_created = 0
        skipped = 0
        errors: list[str] = []

        to_schedule = await self._load_entries_to_schedule(now=now, lookahead=lookahead, grace=grace)
        for entry in to_schedule:
            try:
                await self.draft_service.schedule_protocol_draft(entry, force=True)
                scheduled += 1
            except Exception as exc:
                errors.append(f"{entry.memo_ref_key}: schedule failed: {exc}")
                logger.exception(
                    "meeting.protocol_draft.dispatch_schedule_failed",
                    memo_ref_key=entry.memo_ref_key,
                )

        to_catchup = await self._load_entries_to_catchup(now=now, grace=grace)
        for entry in to_catchup:
            try:
                result = await self.draft_service.create_protocol_draft_for_entry(entry.id)
                if result.get("created"):
                    catchup_created += 1
                else:
                    skipped += 1
            except Exception as exc:
                errors.append(f"{entry.memo_ref_key}: catchup failed: {exc}")
                logger.exception(
                    "meeting.protocol_draft.dispatch_catchup_failed",
                    memo_ref_key=entry.memo_ref_key,
                )

        await self.db.flush()
        logger.info(
            "meeting.protocol_draft.dispatch_completed",
            scheduled=scheduled,
            catchup_created=catchup_created,
            skipped=skipped,
            errors=len(errors),
        )
        return ProtocolDraftDispatchResult(
            scheduled=scheduled,
            catchup_created=catchup_created,
            skipped=skipped,
            errors=errors,
        )

    async def _load_entries_to_schedule(
        self,
        *,
        now: datetime,
        lookahead: datetime,
        grace: datetime,
    ) -> list[MeetingRegistryEntry]:
        result = await self.db.execute(
            select(MeetingRegistryEntry).where(
                and_(
                    MeetingRegistryEntry.slot_start.is_not(None),
                    MeetingRegistryEntry.protocol_draft_at.is_not(None),
                    MeetingRegistryEntry.protocol_draft_at <= lookahead,
                    MeetingRegistryEntry.protocol_draft_at > grace,
                    MeetingRegistryEntry.protocol_ref_key.is_(None),
                    MeetingRegistryEntry.protocol_draft_celery_task_id.is_(None),
                    MeetingRegistryEntry.stage.in_(
                        [MeetingRegistryStage.SCHEDULED, MeetingRegistryStage.INVITATIONS_SENT]
                    ),
                )
            )
        )
        return list(result.scalars().all())

    async def _load_entries_to_catchup(
        self,
        *,
        now: datetime,
        grace: datetime,
    ) -> list[MeetingRegistryEntry]:
        result = await self.db.execute(
            select(MeetingRegistryEntry).where(
                and_(
                    MeetingRegistryEntry.slot_start.is_not(None),
                    MeetingRegistryEntry.protocol_draft_at.is_not(None),
                    MeetingRegistryEntry.protocol_draft_at <= now,
                    MeetingRegistryEntry.protocol_draft_at >= grace,
                    MeetingRegistryEntry.protocol_ref_key.is_(None),
                    MeetingRegistryEntry.stage.in_(
                        [MeetingRegistryStage.SCHEDULED, MeetingRegistryStage.INVITATIONS_SENT]
                    ),
                    or_(
                        MeetingRegistryEntry.protocol_draft_celery_task_id.is_not(None),
                        MeetingRegistryEntry.protocol_draft_celery_task_id.is_(None),
                    ),
                )
            )
        )
        return list(result.scalars().all())


async def run_protocol_draft_dispatch() -> dict[str, object]:
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await MeetingProtocolDispatchService(db).dispatch_due_entries()
        await db.commit()
        return {
            "scheduled": result.scheduled,
            "catchup_created": result.catchup_created,
            "skipped": result.skipped,
            "errors": result.errors,
        }
