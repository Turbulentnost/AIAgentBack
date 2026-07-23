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
from app.models.meeting_category import MeetingCategory
from app.models.scheduled_meeting import ScheduledMeeting, ScheduledMeetingParticipant
from app.models.position import Position
from app.schemas.scheduled_meeting import (
    MeetingCategoryRead,
    ScheduledMeetingAppliedChangesRead,
    ScheduledMeetingCreate,
    ScheduledMeetingDetailRead,
    ScheduledMeetingOccurrenceRead,
    ScheduledMeetingParticipantOptionRead,
    ScheduledMeetingParticipantRead,
    ScheduledMeetingParticipantCreate,
    ScheduledMeetingRead,
    ScheduledMeetingUpdate,
    ScheduledMeetingUpdateRead,
)
from app.services.scheduled_meeting_diff import build_series_update_change_set
from app.services.scheduled_meeting_occurrences import recurrence_input_from_meeting
from app.services.scheduled_meeting_outlook import (
    ScheduledMeetingOutlookError,
    _attendee_emails,
    plan_scheduled_meeting_in_outlook,
    resolve_attendees_for_position_titles,
    resolve_removed_emails_from_outlook_series,
)
from app.services.scheduled_meeting_outlook_participants import sync_series_participants_in_outlook
from app.services.scheduled_meeting_outlook_update import update_series_end_date_in_outlook
from app.services.scheduled_meeting_recurrence import (
    build_recurrence_rule,
    format_recurrence_label,
    iter_occurrence_dates,
)
from app.services.scheduled_meeting_roles import (
    merge_scheduled_meeting_participants,
    protected_participant_position_ids,
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

    async def list_category_options(self) -> list[MeetingCategoryRead]:
        result = await self.db.execute(
            select(MeetingCategory)
            .where(MeetingCategory.is_active.is_(True))
            .order_by(MeetingCategory.sort_order.asc(), MeetingCategory.name.asc())
        )
        return [MeetingCategoryRead.model_validate(category) for category in result.scalars().all()]

    async def list(self) -> list[ScheduledMeetingRead]:
        result = await self.db.execute(
            select(ScheduledMeeting)
            .options(
                selectinload(ScheduledMeeting.meeting_category),
                selectinload(ScheduledMeeting.manager_position),
                selectinload(ScheduledMeeting.responsible_position),
                selectinload(ScheduledMeeting.participants).selectinload(
                    ScheduledMeetingParticipant.position
                ),
            )
            .order_by(ScheduledMeeting.title.asc(), ScheduledMeeting.created_at.asc())
        )
        meetings = result.scalars().all()
        return [self.to_read(meeting) for meeting in meetings]

    async def create(self, payload: ScheduledMeetingCreate) -> ScheduledMeetingRead:
        recurrence_input = payload.resolved_recurrence_input()
        await self._ensure_meeting_category_exists(payload.meeting_category_id)
        merged_participants = merge_scheduled_meeting_participants(
            payload.participants,
            manager_position_id=payload.manager_position_id,
            responsible_position_id=payload.responsible_position_id,
        )
        position_ids = [item.position_id for item in merged_participants if item.position_id]
        await self._ensure_positions_exist(position_ids)

        meeting = ScheduledMeeting(
            title=payload.title.strip(),
            meeting_category_id=payload.meeting_category_id,
            manager_position_id=payload.manager_position_id,
            responsible_position_id=payload.responsible_position_id,
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
            recurrence_label=(
                payload.recurrence_label.strip()
                if payload.recurrence_label and payload.recurrence_label.strip()
                else format_recurrence_label(recurrence_input)
            ),
            recurrence_rule=build_recurrence_rule(recurrence_input),
            payload=payload.resolved_payload(),
        )
        self.db.add(meeting)
        await self.db.flush()

        for index, participant in enumerate(merged_participants):
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

    async def update(
        self,
        meeting_id: uuid.UUID,
        payload: ScheduledMeetingUpdate,
    ) -> ScheduledMeetingUpdateRead:
        meeting = await self._load_meeting(meeting_id)
        if meeting is None:
            raise ScheduledMeetingServiceError("Серия совещаний не найдена", status_code=404)
        if meeting.status == ScheduledMeetingStatus.ARCHIVE:
            raise ScheduledMeetingServiceError(
                "Нельзя изменять архивную серию совещаний",
                status_code=409,
            )

        change_set = build_series_update_change_set(meeting, payload)
        if change_set.unsupported_fields:
            fields = ", ".join(change_set.unsupported_fields)
            raise ScheduledMeetingServiceError(
                "Пока поддерживается изменение срока серии, комментария и состава участников "
                "(добавление и удаление). "
                f"Не поддерживается: {fields}",
                status_code=400,
            )

        if change_set.new_series_end_date < meeting.series_start_date:
            raise ScheduledMeetingServiceError(
                "Дата окончания серии не может быть раньше даты начала",
                status_code=400,
            )

        if (
            not change_set.series_end_changed
            and not change_set.comment_changed
            and not change_set.participants_changed
            and not change_set.meeting_category_changed
            and not change_set.manager_changed
            and not change_set.responsible_changed
        ):
            return ScheduledMeetingUpdateRead(
                series=self.to_read(meeting),
                applied_changes=ScheduledMeetingAppliedChangesRead(
                    db_updated=False,
                    outlook_updated=False,
                    changes=[],
                    outlook_actions=[],
                ),
            )

        outlook_updated = False
        outlook_actions: list[str] = []
        changes: list[str] = []
        participants_added_names: list[str] = []
        participants_removed_names: list[str] = []

        if change_set.meeting_category_changed and change_set.new_meeting_category_id is not None:
            await self._ensure_meeting_category_exists(change_set.new_meeting_category_id)
            meeting.meeting_category_id = change_set.new_meeting_category_id
            changes.append("meeting_category")

        role_participants_need_sync = False
        if change_set.manager_changed and change_set.new_manager_position_id is not None:
            meeting.manager_position_id = change_set.new_manager_position_id
            changes.append("manager")
            role_participants_need_sync = True
        if change_set.responsible_changed and change_set.new_responsible_position_id is not None:
            meeting.responsible_position_id = change_set.new_responsible_position_id
            changes.append("responsible")
            role_participants_need_sync = True

        participant_add_ids: tuple[uuid.UUID, ...] = ()
        participant_remove_ids: tuple[uuid.UUID, ...] = ()
        participants_updated = False

        if role_participants_need_sync:
            merged_participants = merge_scheduled_meeting_participants(
                list(change_set.new_participants)
                if change_set.new_participants
                else [
                    ScheduledMeetingParticipantCreate(
                        position_id=participant.position_id,
                        sort_order=participant.sort_order,
                        is_required=participant.is_required,
                    )
                    for participant in sorted(meeting.participants, key=lambda item: item.sort_order)
                ],
                manager_position_id=meeting.manager_position_id,
                responsible_position_id=meeting.responsible_position_id,
            )
            current_ids = [
                participant.position_id
                for participant in sorted(meeting.participants, key=lambda item: item.sort_order)
            ]
            new_ids = [item.position_id for item in merged_participants if item.position_id]
            participant_add_ids = tuple(
                position_id for position_id in new_ids if position_id not in set(current_ids)
            )
            participant_remove_ids = tuple(
                position_id for position_id in current_ids if position_id not in set(new_ids)
            )
            participants_added_names, participants_removed_names = await self._apply_participant_changes(
                meeting,
                participants=tuple(merged_participants),
                added_position_ids=participant_add_ids,
                removed_position_ids=participant_remove_ids,
            )
            if participant_add_ids or participant_remove_ids or current_ids != new_ids:
                participants_updated = True
                changes.append("participants")

        if change_set.participants_changed and not role_participants_need_sync:
            new_ids = {
                item.position_id
                for item in change_set.new_participants
                if item.position_id is not None
            }
            if not protected_participant_position_ids(meeting).issubset(new_ids):
                raise ScheduledMeetingServiceError(
                    "Нельзя удалить руководителя или ответственного из состава участников",
                    status_code=400,
                )
            merged_participants = merge_scheduled_meeting_participants(
                list(change_set.new_participants),
                manager_position_id=meeting.manager_position_id,
                responsible_position_id=meeting.responsible_position_id,
            )
            participant_add_ids = change_set.participants_added
            participant_remove_ids = change_set.participants_removed
            participants_added_names, participants_removed_names = await self._apply_participant_changes(
                meeting,
                participants=tuple(merged_participants),
                added_position_ids=participant_add_ids,
                removed_position_ids=participant_remove_ids,
            )
            participants_updated = True
            changes.append("participants")

        if (
            participants_updated
            and meeting.outlook_series_id
            and meeting.status == ScheduledMeetingStatus.PLANNED
        ):
            add_emails: list[str] = []
            remove_emails: list[str] = []
            try:
                add_emails = (
                    await self._resolve_emails_for_position_ids(participant_add_ids)
                    if participant_add_ids
                    else []
                )
                remove_emails = (
                    await resolve_removed_emails_from_outlook_series(
                        self.db,
                        meeting,
                        participant_remove_ids,
                    )
                    if participant_remove_ids
                    else []
                )
            except ScheduledMeetingOutlookError as exc:
                raise ScheduledMeetingServiceError(
                    str(exc),
                    status_code=exc.status_code,
                ) from exc
            if add_emails or remove_emails:
                try:
                    outlook_result = await sync_series_participants_in_outlook(
                        self.db,
                        meeting,
                        add_emails=add_emails,
                        remove_emails=remove_emails,
                    )
                except ScheduledMeetingOutlookError as exc:
                    raise ScheduledMeetingServiceError(
                        str(exc),
                        status_code=exc.status_code,
                    ) from exc
                outlook_updated = True
                action = outlook_result.get("action")
                if isinstance(action, str) and action:
                    outlook_actions.append(action)
                changekey = outlook_result.get("outlook_changekey")
                if isinstance(changekey, str) and changekey.strip():
                    meeting.outlook_changekey = changekey

        if change_set.series_end_changed:
            if meeting.outlook_series_id and meeting.status == ScheduledMeetingStatus.PLANNED:
                try:
                    outlook_result = await update_series_end_date_in_outlook(
                        self.db,
                        meeting,
                        new_end_date=change_set.new_series_end_date,
                    )
                except ScheduledMeetingOutlookError as exc:
                    raise ScheduledMeetingServiceError(
                        str(exc),
                        status_code=exc.status_code,
                    ) from exc
                outlook_updated = True
                action = outlook_result.get("action")
                if isinstance(action, str) and action:
                    outlook_actions.append(action)
                meeting.outlook_changekey = outlook_result.get("outlook_changekey") or meeting.outlook_changekey
                meeting.outlook_meeting_url = (
                    outlook_result.get("outlook_meeting_url") or meeting.outlook_meeting_url
                )

            meeting.series_end_date = change_set.new_series_end_date
            recurrence_input = recurrence_input_from_meeting(meeting)
            meeting.recurrence_rule = build_recurrence_rule(recurrence_input)
            meeting.recurrence_label = format_recurrence_label(recurrence_input)
            changes.append("series_end_date")

        if change_set.comment_changed:
            stored_payload = dict(meeting.payload or {})
            new_comment = (payload.comment or "").strip()
            if new_comment:
                stored_payload["comment"] = new_comment
            else:
                stored_payload.pop("comment", None)
            meeting.payload = stored_payload or None
            changes.append("comment")

        await self.db.flush()

        if (change_set.series_end_changed or change_set.participants_changed) and (
            meeting.status == ScheduledMeetingStatus.PLANNED
        ):
            from app.services.scheduled_meeting_registry_sync import (
                ScheduledMeetingRegistrySyncService,
            )

            try:
                await ScheduledMeetingRegistrySyncService(self.db).sync_series_card(meeting.id)
            except Exception:
                logger.warning(
                    "scheduled_series_registry_sync_after_update_failed meeting_id=%s",
                    meeting.id,
                    exc_info=True,
                )

        loaded = await self._load_meeting(meeting.id)
        if loaded is None:
            raise ScheduledMeetingServiceError("Не удалось обновить серию совещаний", status_code=500)

        return ScheduledMeetingUpdateRead(
            series=self.to_read(loaded),
            applied_changes=ScheduledMeetingAppliedChangesRead(
                db_updated=True,
                outlook_updated=outlook_updated,
                changes=changes,
                outlook_actions=outlook_actions,
                participants_added=participants_added_names,
                participants_removed=participants_removed_names,
            ),
        )

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
        upcoming_items = [
            item
            for item in sorted(occurrences, key=lambda entry: entry.slot_start)
            if item.slot_end >= now and not item.is_cancelled
        ]
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
        upcoming_occurrences = [
            ScheduledMeetingOccurrenceRead(
                **occurrence_to_read(item, outlook_meeting_url=series_url, source=source)
            )
            for item in upcoming_items
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
            upcoming_occurrences=upcoming_occurrences,
            past_occurrences=past_occurrences,
            current_card=current_card,
            history=history,
        )

    async def _position_names_for_ids(self, position_ids: tuple[uuid.UUID, ...]) -> list[str]:
        if not position_ids:
            return []
        result = await self.db.execute(select(Position).where(Position.id.in_(position_ids)))
        names_by_id = {
            position.id: position.name.strip()
            for position in result.scalars().all()
            if position.name and position.name.strip()
        }
        return [names_by_id[position_id] for position_id in position_ids if position_id in names_by_id]

    async def _resolve_emails_for_position_ids(
        self,
        position_ids: tuple[uuid.UUID, ...],
    ) -> list[str]:
        if not position_ids:
            return []
        result = await self.db.execute(select(Position).where(Position.id.in_(position_ids)))
        positions = list(result.scalars().all())
        titles = [position.name.strip() for position in positions if position.name and position.name.strip()]
        if len(titles) != len(position_ids):
            missing = [str(position_id) for position_id in position_ids]
            raise ScheduledMeetingServiceError(
                f"Не найдены должности для участников: {', '.join(missing)}",
                status_code=400,
            )
        attendees = await resolve_attendees_for_position_titles(self.db, titles)
        return _attendee_emails(attendees)

    async def _apply_participant_changes(
        self,
        meeting: ScheduledMeeting,
        *,
        participants: tuple[ScheduledMeetingParticipantCreate, ...],
        added_position_ids: tuple[uuid.UUID, ...],
        removed_position_ids: tuple[uuid.UUID, ...],
    ) -> tuple[list[str], list[str]]:
        position_ids = [item.position_id for item in participants if item.position_id]
        await self._ensure_positions_exist(position_ids)
        added_set = set(added_position_ids)
        removed_set = set(removed_position_ids)
        existing_by_position = {item.position_id: item for item in meeting.participants}

        for position_id in removed_position_ids:
            existing = existing_by_position.get(position_id)
            if existing is not None:
                await self.db.delete(existing)

        for index, participant in enumerate(participants):
            position_id = participant.position_id
            if position_id is None:
                continue
            if position_id in added_set:
                self.db.add(
                    ScheduledMeetingParticipant(
                        scheduled_meeting_id=meeting.id,
                        position_id=position_id,
                        sort_order=participant.sort_order if participant.sort_order else index,
                        is_required=participant.is_required,
                    )
                )
                continue
            existing = existing_by_position.get(position_id)
            if existing is not None:
                existing.sort_order = participant.sort_order if participant.sort_order else index
                existing.is_required = participant.is_required

        added_names = await self._position_names_for_ids(added_position_ids)
        removed_names = await self._position_names_for_ids(removed_position_ids)
        return added_names, removed_names

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

    async def _ensure_meeting_category_exists(self, category_id: uuid.UUID) -> None:
        category = await self.db.get(MeetingCategory, category_id)
        if category is None or not category.is_active:
            raise ScheduledMeetingServiceError(
                f"Не найден активный вид совещания: {category_id}",
                status_code=400,
            )

    async def _load_meeting(self, meeting_id: uuid.UUID) -> ScheduledMeeting | None:
        result = await self.db.execute(
            select(ScheduledMeeting)
            .where(ScheduledMeeting.id == meeting_id)
            .options(
                selectinload(ScheduledMeeting.meeting_category),
                selectinload(ScheduledMeeting.manager_position),
                selectinload(ScheduledMeeting.responsible_position),
                selectinload(ScheduledMeeting.participants).selectinload(
                    ScheduledMeetingParticipant.position
                ),
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
        recurrence_input = recurrence_input_from_meeting(meeting)
        return ScheduledMeetingRead(
            id=meeting.id,
            title=meeting.title,
            meeting_category_id=meeting.meeting_category_id,
            meeting_category_name=(
                meeting.meeting_category.name if meeting.meeting_category is not None else None
            ),
            manager_position_id=meeting.manager_position_id,
            manager_position_name=(
                meeting.manager_position.name if meeting.manager_position is not None else None
            ),
            responsible_position_id=meeting.responsible_position_id,
            responsible_position_name=(
                meeting.responsible_position.name
                if meeting.responsible_position is not None
                else None
            ),
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
            occurrence_count=len(iter_occurrence_dates(recurrence_input)),
            recurrence_rule=meeting.recurrence_rule,
            outlook_series_id=meeting.outlook_series_id,
            outlook_changekey=meeting.outlook_changekey,
            outlook_meeting_url=meeting.outlook_meeting_url,
            payload=meeting.payload,
            participants=participants,
        )
