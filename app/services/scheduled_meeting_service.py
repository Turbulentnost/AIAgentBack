from __future__ import annotations

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.enums import ScheduledMeetingStatus
from app.models.scheduled_meeting import ScheduledMeeting, ScheduledMeetingParticipant
from app.models.user import Department
from app.schemas.scheduled_meeting import (
    ScheduledMeetingCreate,
    ScheduledMeetingDetailRead,
    ScheduledMeetingParticipantOptionRead,
    ScheduledMeetingParticipantRead,
    ScheduledMeetingRead,
)
from app.utils.department_classification import is_schedule_participant_department_name
from app.utils.department_utils import is_liquidated_department_name
from app.services.scheduled_meeting_outlook import (
    ScheduledMeetingOutlookError,
    plan_scheduled_meeting_in_outlook,
)
from app.services.scheduled_meeting_recurrence import (
    build_recurrence_rule,
    format_recurrence_label,
)


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
        from app.services.user_service import DepartmentService

        departments = await DepartmentService(self.db).list_schedule_participant_options(
            search=search,
            limit=limit,
        )
        return [
            ScheduledMeetingParticipantOptionRead(
                id=department.id,
                name=department.name.strip(),
            )
            for department in departments
            if department.name.strip()
        ]

    async def list(self) -> list[ScheduledMeetingRead]:
        result = await self.db.execute(
            select(ScheduledMeeting)
            .options(
                selectinload(ScheduledMeeting.participants).selectinload(
                    ScheduledMeetingParticipant.department
                )
            )
            .order_by(ScheduledMeeting.title.asc(), ScheduledMeeting.created_at.asc())
        )
        meetings = result.scalars().all()
        return [self.to_read(meeting) for meeting in meetings]

    async def create(self, payload: ScheduledMeetingCreate) -> ScheduledMeetingRead:
        recurrence_input = payload.resolved_recurrence_input()
        department_ids = [item.department_id for item in payload.participants]
        await self._ensure_departments_exist(department_ids)

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
                    department_id=participant.department_id,
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

    async def get_detail(self, meeting_id: uuid.UUID) -> ScheduledMeetingDetailRead:
        from app.services.meeting_mappers import registry_event_read, registry_item_read
        from app.services.meeting_registry_service import MeetingRegistryService
        from app.services.scheduled_meeting_registry_sync import ScheduledMeetingRegistrySyncService

        meeting = await self._load_meeting(meeting_id)
        if meeting is None:
            raise ScheduledMeetingServiceError("Серия совещаний не найдена", status_code=404)

        sync_result = await ScheduledMeetingRegistrySyncService(self.db).sync_series_card(meeting_id)
        registry = MeetingRegistryService(self.db)
        entry = await registry.get_entry_by_scheduled_meeting_id(meeting_id)
        history = []
        current_card = None
        if entry is not None:
            current_card = registry_item_read(entry)
            events = await registry.list_events(entry.memo_ref_key)
            history = [registry_event_read(item) for item in events]

        return ScheduledMeetingDetailRead(
            series=self.to_read(meeting),
            current_card=current_card,
            history=history,
            next_occurrence_date=sync_result.occurrence_date,
            sync_source=sync_result.sync_source,
            sync_action=sync_result.action,
        )

    async def _ensure_departments_exist(self, department_ids: list[uuid.UUID]) -> None:
        if not department_ids:
            return
        unique_ids = list(dict.fromkeys(department_ids))
        result = await self.db.execute(select(Department).where(Department.id.in_(unique_ids)))
        departments = {department.id: department for department in result.scalars().all()}
        missing: list[str] = []
        for department_id in unique_ids:
            department = departments.get(department_id)
            if department is None:
                missing.append(str(department_id))
                continue
            if is_liquidated_department_name(department.name):
                missing.append(str(department_id))
                continue
            if department.is_active:
                continue
            if not is_schedule_participant_department_name(department.name):
                missing.append(str(department_id))
        if missing:
            raise ScheduledMeetingServiceError(
                f"Не найдены активные должности/подразделения: {', '.join(missing)}"
            )

    async def _load_meeting(self, meeting_id: uuid.UUID) -> ScheduledMeeting | None:
        result = await self.db.execute(
            select(ScheduledMeeting)
            .where(ScheduledMeeting.id == meeting_id)
            .options(
                selectinload(ScheduledMeeting.participants).selectinload(
                    ScheduledMeetingParticipant.department
                )
            )
        )
        return result.scalar_one_or_none()

    def to_read(self, meeting: ScheduledMeeting) -> ScheduledMeetingRead:
        participants = [
            ScheduledMeetingParticipantRead(
                id=participant.id,
                department_id=participant.department_id,
                department_name=(
                    participant.department.name if participant.department is not None else None
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
