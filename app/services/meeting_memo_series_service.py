"""Создание серии совещаний в графике из служебной записки."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ScheduledMeetingType, ScheduledMeetingStatus
from app.models.meeting_category import MeetingCategory
from app.models.scheduled_meeting import ScheduledMeeting
from app.models.user import User
from app.schemas.meeting import MeetingMemoApproveRequest
from app.schemas.scheduled_meeting import (
    ScheduledMeetingCreate,
    ScheduledMeetingParticipantCreate,
    ScheduledMeetingRecurrencePayload,
    ScheduledMeetingRead,
)
from app.services.scheduled_meeting_person import (
    ScheduledMeetingPersonError,
    resolve_person,
)
from app.services.meeting_memo_cache import (
    MemoCacheMissError,
    MeetingMemoCacheService,
    document_from_cached_detail,
)
from app.services.meeting_memo_recurrence import MemoRecurrenceDraft
from app.services.meeting_memo_series_llm import resolve_memo_recurrence_async
from app.services.meeting_schedule_categories import MEETING_CATEGORY_NAMES
from app.services.meeting_service import MeetingService, MeetingServiceError
from app.services.scheduled_meeting_service import (
    ScheduledMeetingService,
    ScheduledMeetingServiceError,
)

_MEETING_TYPE_FROM_1C: dict[str, ScheduledMeetingType] = {
    "Плановое": ScheduledMeetingType.PLANNED,
    "Отчетное": ScheduledMeetingType.REPORT,
    "Отчётное": ScheduledMeetingType.REPORT,
    "Селекторное": ScheduledMeetingType.SELECTOR,
    "Внеплановое": ScheduledMeetingType.UNPLANNED,
}


class MeetingMemoSeriesServiceError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class MemoSeriesCreateResult:
    scheduled_meeting: ScheduledMeetingRead
    occurrence_count: int | None
    memo_approved: bool
    memo_approve_message: str | None


class MeetingMemoSeriesService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_series_from_memo(
        self,
        memo_ref_key: str,
        *,
        current_user: User,
        meeting_topic: dict[str, Any] | None = None,
    ) -> MemoSeriesCreateResult:
        normalized_ref = memo_ref_key.strip().lower()
        cache = MeetingMemoCacheService()
        try:
            detail, _, _ = await cache.get_memo_detail(normalized_ref)
        except MemoCacheMissError as exc:
            raise MeetingMemoSeriesServiceError(str(exc), status_code=503) from exc

        existing = await self._find_existing_series(normalized_ref)
        if existing is not None:
            scheduled = ScheduledMeetingService(self.db).to_read(existing)
            return MemoSeriesCreateResult(
                scheduled_meeting=scheduled,
                occurrence_count=scheduled.occurrence_count,
                memo_approved=False,
                memo_approve_message="Серия для этой СЗ уже создана в графике",
            )

        document = document_from_cached_detail(detail)
        header = document.get("header") or {}
        draft = await resolve_memo_recurrence_async(
            header,
            document,
            ref_key=normalized_ref,
        )
        if not draft.is_series or draft.recurrence is None:
            raise MeetingMemoSeriesServiceError(
                "В служебной записке не распознана серия совещаний",
                status_code=422,
            )
        if draft.confidence == "low":
            raise MeetingMemoSeriesServiceError(
                "Периодичность серии распознана с низкой уверенностью — "
                "запланируйте совещание как единоразовое",
                status_code=422,
            )

        application = detail.get("application") or {}
        manager = application.get("manager")
        initiator = application.get("initiator")
        participants_raw = application.get("participants") or []

        manager_person = await self._resolve_person_for_memo_participant(
            manager,
            role_label="руководитель",
        )
        responsible_person = await self._resolve_person_for_memo_participant(
            initiator or manager,
            role_label="ответственный",
        )

        participant_people: list[ScheduledMeetingParticipantCreate] = []
        seen_user_ids: set[uuid.UUID] = set()
        for participant in participants_raw:
            if not isinstance(participant, dict):
                continue
            try:
                person = await self._resolve_person_for_memo_participant(
                    participant,
                    role_label="участник",
                )
            except MeetingMemoSeriesServiceError:
                continue
            if person.user_id in seen_user_ids:
                continue
            if person.user_id in {manager_person.user_id, responsible_person.user_id}:
                continue
            seen_user_ids.add(person.user_id)
            participant_people.append(
                ScheduledMeetingParticipantCreate(
                    user_id=person.user_id,
                    person_fio=person.fio,
                    person_email=person.email,
                    position_id=person.position_id,
                    sort_order=len(participant_people),
                )
            )

        category = await self._resolve_meeting_category(
            detail.get("title") or detail.get("subject") or ""
        )
        meeting_type = self._resolve_meeting_type(application.get("meeting_type"))
        recurrence = draft.recurrence
        series_title = self._resolve_series_title(detail, meeting_topic)
        series_payload: dict[str, Any] = {
            "memo_ref_key": normalized_ref,
            "memo_number": detail.get("number"),
            "source": "memo_series",
            "recurrence_label": draft.recurrence_label,
        }
        if isinstance(meeting_topic, dict):
            topic_ref = str(meeting_topic.get("ref_key") or "").strip()
            if topic_ref:
                series_payload["meeting_topic_ref_key"] = topic_ref
            series_payload["meeting_topic"] = meeting_topic

        payload = ScheduledMeetingCreate(
            title=series_title,
            meeting_category_id=category.id,
            manager_user_id=manager_person.user_id,
            responsible_user_id=responsible_person.user_id,
            manager_position_id=manager_person.position_id,
            responsible_position_id=responsible_person.position_id,
            meeting_type=meeting_type,
            status=ScheduledMeetingStatus.CREATED,
            series_start_date=recurrence.series_start_date,
            series_end_date=recurrence.series_end_date,
            recurrence_label=draft.recurrence_label,
            recurrence=ScheduledMeetingRecurrencePayload(
                frequency=recurrence.frequency,
                interval=recurrence.interval,
                time_local=recurrence.time_local,
                duration_minutes=recurrence.duration_minutes,
                monthly_mode=recurrence.monthly_mode,
                day_of_month=recurrence.day_of_month,
                weekday=recurrence.weekday,
                weekday_position=recurrence.weekday_position,
                series_start_date=recurrence.series_start_date,
                series_end_date=recurrence.series_end_date,
            ),
            participants=participant_people,
            comment=(application.get("agenda") or None),
            payload=series_payload,
        )

        await cache.set_series_planning_choice(normalized_ref, "series")
        scheduled_service = ScheduledMeetingService(self.db)
        try:
            created = await scheduled_service.create(payload)
        except ScheduledMeetingServiceError as exc:
            raise MeetingMemoSeriesServiceError(str(exc), status_code=exc.status_code) from exc

        try:
            approve_result = await MeetingService(self.db).approve_memo(
                normalized_ref,
                MeetingMemoApproveRequest(),
                current_user=current_user,
            )
        except MeetingServiceError as exc:
            raise MeetingMemoSeriesServiceError(str(exc), status_code=exc.status_code) from exc

        return MemoSeriesCreateResult(
            scheduled_meeting=created,
            occurrence_count=draft.occurrence_count or created.occurrence_count,
            memo_approved=bool(approve_result.changed or approve_result.already_approved),
            memo_approve_message=approve_result.message,
        )

    async def _find_existing_series(self, memo_ref_key: str) -> ScheduledMeeting | None:
        result = await self.db.execute(
            select(ScheduledMeeting)
            .where(ScheduledMeeting.payload["memo_ref_key"].astext == memo_ref_key)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _resolve_meeting_category(self, title: str) -> MeetingCategory:
        result = await self.db.execute(
            select(MeetingCategory)
            .where(MeetingCategory.is_active.is_(True))
            .order_by(MeetingCategory.sort_order.asc(), MeetingCategory.name.asc())
        )
        categories = list(result.scalars().all())
        if not categories:
            raise MeetingMemoSeriesServiceError(
                "Справочник видов совещаний пуст — добавьте категории в график",
                status_code=503,
            )

        normalized_title = (title or "").casefold().replace("ё", "е")
        for category in categories:
            name = category.name.casefold().replace("ё", "е")
            if name and name in normalized_title:
                return category

        for default_name in MEETING_CATEGORY_NAMES:
            for category in categories:
                if category.name == default_name:
                    return category
        return categories[0]

    def _resolve_meeting_type(self, raw: Any) -> ScheduledMeetingType:
        if isinstance(raw, str):
            mapped = _MEETING_TYPE_FROM_1C.get(raw.strip())
            if mapped is not None:
                return mapped
        return ScheduledMeetingType.PLANNED

    @staticmethod
    def _resolve_series_title(
        detail: dict[str, Any],
        meeting_topic: dict[str, Any] | None,
    ) -> str:
        fallback = (detail.get("title") or detail.get("subject") or "Совещание по СЗ").strip()
        if not isinstance(meeting_topic, dict):
            return fallback
        description = str(meeting_topic.get("description") or "").strip()
        if description and (meeting_topic.get("used_existing") or meeting_topic.get("created")):
            return description
        return fallback

    async def _resolve_person_for_memo_participant(
        self,
        person: dict[str, Any] | None,
        *,
        role_label: str,
    ):
        if not isinstance(person, dict):
            raise MeetingMemoSeriesServiceError(f"Не указан {role_label}")
        try:
            return await resolve_person(self.db, memo_person=person)
        except ScheduledMeetingPersonError as exc:
            raise MeetingMemoSeriesServiceError(str(exc), status_code=exc.status_code) from exc
