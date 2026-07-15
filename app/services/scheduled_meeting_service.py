from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

import asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.enums import ScheduledMeetingStatus
from app.models.scheduled_meeting import ScheduledMeeting, ScheduledMeetingParticipant
from app.models.position import Position
from app.schemas.scheduled_meeting import (
    ScheduledMeetingCreate,
    ScheduledMeetingDetailRead,
    ScheduledMeetingOccurrenceRead,
    ScheduledMeetingParticipantOptionRead,
    ScheduledMeetingParticipantRead,
    ScheduledMeetingRead,
)
from app.services.scheduled_meeting_recurrence import (
    build_recurrence_rule,
    format_recurrence_label,
)
from app.services.scheduled_meeting_outlook import (
    ScheduledMeetingOutlookError,
    plan_scheduled_meeting_in_outlook,
)

logger = logging.getLogger(__name__)


class ScheduledMeetingServiceError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class ScheduledMeetingService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_participant_options(
        self,
        *,
        search: str | None = None,
        limit: int = 100,
    ) -> list[ScheduledMeetingParticipantOptionRead]:
        from app.services.position_service import PositionService

        positions = await PositionService(self.db).list(
            search=search,
            limit=limit,
            active_only=True,
        )
        return [
            ScheduledMeetingParticipantOptionRead(
                id=position.id,
                name=position.name.strip(),
                slug=position.slug,
            )
            for position in positions
            if position.name.strip()
        ]

    async def list(self) -> list[ScheduledMeetingRead]:
        result = await self.db.execute(
            select(ScheduledMeeting)
            .options(
                selectinload(ScheduledMeeting.participants).selectinload(
                    ScheduledMeetingParticipant.position
                )
            )
            .order_by(ScheduledMeeting.title.asc(), ScheduledMeeting.created_at.asc())
        )
        meetings = result.scalars().all()
        return [self.to_read(meeting) for meeting in meetings]

    async def create(self, payload: ScheduledMeetingCreate) -> ScheduledMeetingRead:
        recurrence_input = payload.resolved_recurrence_input()
        position_ids = [item.position_id for item in payload.participants if item.position_id]
        await self._ensure_positions_exist(position_ids)

        meeting = ScheduledMeeting(
            title=payload.title.strip(),
            meeting_type=payload.meeting_type,
            status=payload.status,
            time_local=recurrence_input.time_local,
            duration_minutes=recurrence_input.duration_minutes,
            frequency=recurrence_input.frequency,
            interval=recurrence_input.interval,
            monthly_mode=recurrence_input.monthly_mode,
            day_of_month=recurrence_input.day_of_month,
            weekday=recurrence_input.weekday,
            weekday_position=recurrence_input.weekday_position,
            series_start_date=recurrence_input.series_start_date,
            series_end_date=recurrence_input.series_end_date,
            recurrence_label=format_recurrence_label(recurrence_input),
            recurrence_rule=build_recurrence_rule(recurrence_input),
            payload=payload.resolved_payload(),
        )
        self.db.add(meeting)
        await self.db.flush()

        for index, participant in enumerate(payload.participants):
            self.db.add(
                ScheduledMeetingParticipant(
                    scheduled_meeting_id=meeting.id,
                    position_id=participant.position_id,
                    sort_order=participant.sort_order if participant.sort_order else index,
                    is_required=participant.is_required,
                )
            )

        await self.db.flush()
        loaded = await self._load_meeting(meeting.id)
        if loaded is None:
            raise ScheduledMeetingServiceError("Не удалось сохранить серию совещаний", status_code=500)
        return self.to_read(loaded)

    async def archive_expired_series(self, *, as_of_date: date | None = None) -> dict[str, int | str | list[str]]:
        if as_of_date is None:
            as_of_date = datetime.now(ZoneInfo(settings.OUTLOOK_TIMEZONE)).date()

        result = await self.db.execute(
            update(ScheduledMeeting)
            .where(
                ScheduledMeeting.series_end_date < as_of_date,
                ScheduledMeeting.status != ScheduledMeetingStatus.ARCHIVE,
            )
            .values(status=ScheduledMeetingStatus.ARCHIVE)
            .returning(ScheduledMeeting.id)
        )
        archived_ids = [str(meeting_id) for meeting_id in result.scalars().all()]
        return {
            "archived_count": len(archived_ids),
            "archived_ids": archived_ids,
            "as_of_date": as_of_date.isoformat(),
        }

    async def plan(self, meeting_id: uuid.UUID) -> ScheduledMeetingRead:
        meeting = await self._load_meeting(meeting_id)
        if meeting is None:
            raise ScheduledMeetingServiceError("Серия совещаний не найдена", status_code=404)
        try:
            await plan_scheduled_meeting_in_outlook(self.db, meeting)
        except ScheduledMeetingOutlookError as exc:
            raise ScheduledMeetingServiceError(str(exc), status_code=exc.status_code) from exc

        from app.services.scheduled_meeting_registry_sync import ScheduledMeetingRegistrySyncService

        await ScheduledMeetingRegistrySyncService(self.db).sync_series_card(meeting.id)

        loaded = await self._load_meeting(meeting.id)
        if loaded is None:
            raise ScheduledMeetingServiceError("Не удалось обновить серию совещаний", status_code=500)
        return self.to_read(loaded)

    async def get(self, meeting_id: uuid.UUID) -> ScheduledMeetingRead:
        meeting = await self._load_meeting(meeting_id)
        if meeting is None:
            raise ScheduledMeetingServiceError("Серия совещаний не найдена", status_code=404)
        return self.to_read(meeting)

    async def get_detail(self, meeting_id: uuid.UUID) -> ScheduledMeetingDetailRead:
        from app.services.meeting_mappers import registry_event_read, registry_item_read
        from app.services.meeting_registry_service import MeetingRegistryService
        from app.services.scheduled_meeting_occurrences import (
            find_next_occurrence,
            occurrence_to_read,
            resolve_series_occurrences,
        )

        meeting = await self._load_meeting(meeting_id)
        if meeting is None:
            raise ScheduledMeetingServiceError("Серия совещаний не найдена", status_code=404)

        now = datetime.now(ZoneInfo(settings.OUTLOOK_TIMEZONE))
        occurrences, source = await asyncio.to_thread(
            resolve_series_occurrences,
            meeting,
            range_start=meeting.series_start_date,
            range_end=meeting.series_end_date,
            now=now,
        )

        next_item = find_next_occurrence(occurrences, now=now)
        past_items = [
            item
            for item in sorted(occurrences, key=lambda entry: entry.slot_start, reverse=True)
            if item.slot_end < now
        ]

        series_url = meeting.outlook_meeting_url
        next_occurrence = (
            ScheduledMeetingOccurrenceRead(
                **occurrence_to_read(next_item, outlook_meeting_url=series_url, source=source)
            )
            if next_item is not None
            else None
        )
        past_occurrences = [
            ScheduledMeetingOccurrenceRead(
                **occurrence_to_read(item, outlook_meeting_url=series_url, source=source)
            )
            for item in past_items
        ]

        registry = MeetingRegistryService(self.db)
        entry = await registry.get_entry_by_scheduled_meeting_id(meeting_id)
        if (
            entry is None
            and meeting.status == ScheduledMeetingStatus.PLANNED
            and meeting.outlook_series_id
        ):
            from app.services.scheduled_meeting_registry_sync import (
                ScheduledMeetingRegistrySyncService,
            )

            try:
                await ScheduledMeetingRegistrySyncService(self.db).sync_series_card(meeting_id)
                entry = await registry.get_entry_by_scheduled_meeting_id(meeting_id)
            except Exception:
                logger.warning(
                    "scheduled_series_registry_lazy_sync_failed meeting_id=%s",
                    meeting_id,
                    exc_info=True,
                )
        current_card = registry_item_read(entry) if entry is not None else None
        history = []
        if entry is not None:
            events = await registry.list_events(entry.memo_ref_key)
            history = [registry_event_read(item) for item in events]

        return ScheduledMeetingDetailRead(
            series=self.to_read(meeting),
            next_occurrence=next_occurrence,
            past_occurrences=past_occurrences,
            current_card=current_card,
            history=history,
        )

    async def _ensure_positions_exist(self, position_ids: list[uuid.UUID]) -> None:
        if not position_ids:
            return
        unique_ids = list(dict.fromkeys(position_ids))
        result = await self.db.execute(select(Position).where(Position.id.in_(unique_ids)))
        positions = {position.id: position for position in result.scalars().all()}
        missing: list[str] = []
        for position_id in unique_ids:
            position = positions.get(position_id)
            if position is None or not position.is_active:
                missing.append(str(position_id))
        if missing:
            raise ScheduledMeetingServiceError(
                f"Не найдены активные должности: {', '.join(missing)}"
            )

    async def _load_meeting(self, meeting_id: uuid.UUID) -> ScheduledMeeting | None:
        result = await self.db.execute(
            select(ScheduledMeeting)
            .where(ScheduledMeeting.id == meeting_id)
            .options(
                selectinload(ScheduledMeeting.participants).selectinload(
                    ScheduledMeetingParticipant.position
                )
            )
        )
        return result.scalar_one_or_none()

    def to_read(self, meeting: ScheduledMeeting) -> ScheduledMeetingRead:
        participants = [
            ScheduledMeetingParticipantRead(
                id=participant.id,
                position_id=participant.position_id,
                position_name=(
                    participant.position.name if participant.position is not None else None
                ),
                department_id=participant.position_id,
                department_name=(
                    participant.position.name if participant.position is not None else None
                ),
                sort_order=participant.sort_order,
                is_required=participant.is_required,
            )
            for participant in sorted(meeting.participants, key=lambda item: item.sort_order)
        ]
        return ScheduledMeetingRead(
            id=meeting.id,
            title=meeting.title,
            meeting_type=meeting.meeting_type,
            status=meeting.status,
            time_local=meeting.time_local,
            duration_minutes=meeting.duration_minutes,
            frequency=meeting.frequency,
            interval=meeting.interval,
            monthly_mode=meeting.monthly_mode,
            day_of_month=meeting.day_of_month,
            weekday=meeting.weekday,
            weekday_position=meeting.weekday_position,
            series_start_date=meeting.series_start_date,
            series_end_date=meeting.series_end_date,
            recurrence_label=meeting.recurrence_label,
            recurrence_rule=meeting.recurrence_rule,
            outlook_series_id=meeting.outlook_series_id,
            outlook_changekey=meeting.outlook_changekey,
            outlook_meeting_url=meeting.outlook_meeting_url,
            payload=meeting.payload,
            participants=participants,
        )
