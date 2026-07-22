from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.enums import (
    MeetingRegistryEventType,
    MeetingRegistryStage,
    ScheduledMeetingStatus,
)
from app.models.meeting_registry import MeetingRegistryEntry
from app.models.scheduled_meeting import ScheduledMeeting, ScheduledMeetingParticipant
from app.services.meeting_registry_service import MeetingRegistryService
from app.services.meeting_slot import format_slot_label
from app.services.scheduled_meeting_occurrences import (
    OccurrenceSource,
    SeriesOccurrence,
    find_next_after,
    find_next_occurrence,
    find_occurrence_on_date,
    resolve_series_occurrences,
)

logger = logging.getLogger(__name__)

SyncAction = Literal["created", "rolled", "updated", "skipped", "no_occurrences"]


@dataclass(frozen=True)
class SyncResult:
    action: SyncAction
    series_id: uuid.UUID
    entry_id: uuid.UUID | None = None
    occurrence_date: date | None = None
    sync_source: OccurrenceSource | Literal["none"] = "none"
    message: str | None = None


@dataclass(frozen=True)
class BatchSyncResult:
    processed: int
    created: int
    rolled: int
    updated: int
    skipped: int
    no_occurrences: int
    errors: list[str]


class ScheduledMeetingRegistrySyncService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _now(self) -> datetime:
        return datetime.now(ZoneInfo(settings.OUTLOOK_TIMEZONE))

    def _today(self) -> date:
        return self._now().date()

    async def _load_series(self, series_id: uuid.UUID) -> ScheduledMeeting | None:
        result = await self.db.execute(
            select(ScheduledMeeting)
            .where(ScheduledMeeting.id == series_id)
            .options(
                selectinload(ScheduledMeeting.participants).selectinload(
                    ScheduledMeetingParticipant.position
                )
            )
        )
        return result.scalar_one_or_none()

    def _participant_names(self, meeting: ScheduledMeeting) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for participant in sorted(meeting.participants, key=lambda item: item.sort_order):
            position = participant.position
            if position is None:
                continue
            name = position.name.strip()
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            names.append(name)
        return names

    def _merge_occurrence_participants(
        self,
        meeting: ScheduledMeeting,
        entry: MeetingRegistryEntry,
    ) -> list[str]:
        """Должности серии + вручную добавленные участники конкретного вхождения."""
        series_names = self._participant_names(meeting)
        series_keys = {name.casefold() for name in series_names}
        merged: list[str] = list(series_names)
        seen = set(series_keys)

        def append_extra(name: str) -> None:
            normalized = name.strip()
            if not normalized:
                return
            key = normalized.casefold()
            if key in seen:
                return
            seen.add(key)
            merged.append(normalized)

        current = entry.participants if isinstance(entry.participants, list) else []
        for name in current:
            if isinstance(name, str) and name.casefold() not in series_keys:
                append_extra(name)

        payload = entry.payload if isinstance(entry.payload, dict) else {}
        stored = payload.get("occurrence_participant_names")
        if isinstance(stored, list):
            for name in stored:
                if isinstance(name, str) and name.casefold() not in series_keys:
                    append_extra(name)

        return merged

    async def _resolve_occurrences(
        self,
        meeting: ScheduledMeeting,
    ) -> tuple[list[SeriesOccurrence], OccurrenceSource | Literal["none"]]:
        return resolve_series_occurrences(
            meeting,
            range_start=self._today(),
            range_end=meeting.series_end_date,
            now=self._now(),
        )

    def _apply_occurrence_to_entry(
        self,
        entry: MeetingRegistryEntry,
        meeting: ScheduledMeeting,
        occurrence: SeriesOccurrence,
    ) -> None:
        participants = self._merge_occurrence_participants(meeting, entry)
        entry.title = meeting.title
        entry.subject = occurrence.subject or meeting.title
        entry.participants = participants
        entry.participants_count = len(participants)
        entry.slot_start = occurrence.slot_start
        entry.slot_end = occurrence.slot_end
        entry.series_occurrence_date = occurrence.occurrence_date
        entry.scheduled_meeting_id = meeting.id
        if occurrence.outlook_item_id:
            entry.outlook_item_id = occurrence.outlook_item_id
        if occurrence.outlook_changekey:
            entry.outlook_changekey = occurrence.outlook_changekey
        if meeting.outlook_meeting_url:
            entry.outlook_meeting_url = meeting.outlook_meeting_url
        payload = dict(entry.payload or {})
        payload.update(
            {
                "source": "scheduled_series",
                "sync_source": occurrence.source,
                "scheduled_meeting_id": str(meeting.id),
                "series_recurrence_label": meeting.recurrence_label,
            }
        )
        entry.payload = payload

    async def _create_entry(
        self,
        meeting: ScheduledMeeting,
        occurrence: SeriesOccurrence,
        *,
        registry: MeetingRegistryService,
    ) -> MeetingRegistryEntry:
        now = datetime.now(timezone.utc)
        entry = MeetingRegistryEntry(
            memo_ref_key=str(uuid.uuid4()),
            title=meeting.title,
            subject=occurrence.subject or meeting.title,
            participants=self._participant_names(meeting),
            participants_count=len(self._participant_names(meeting)),
            slot_start=occurrence.slot_start,
            slot_end=occurrence.slot_end,
            stage=MeetingRegistryStage.SCHEDULED,
            invitations_sent_at=now,
            scheduled_meeting_id=meeting.id,
            series_occurrence_date=occurrence.occurrence_date,
            outlook_item_id=occurrence.outlook_item_id,
            outlook_changekey=occurrence.outlook_changekey,
            outlook_meeting_url=meeting.outlook_meeting_url,
            payload={
                "source": "scheduled_series",
                "sync_source": occurrence.source,
                "scheduled_meeting_id": str(meeting.id),
                "series_recurrence_label": meeting.recurrence_label,
            },
        )
        self.db.add(entry)
        await self.db.flush()
        await registry.append_event(
            entry,
            event_type=MeetingRegistryEventType.STAGE_CHANGED,
            message=(
                f"Создана карточка серии на {format_slot_label(entry.slot_start.isoformat(), entry.slot_end.isoformat() if entry.slot_end else entry.slot_start.isoformat())}"
            ),
            payload={
                "occurrence_date": occurrence.occurrence_date.isoformat(),
                "sync_source": occurrence.source,
            },
        )
        await self.db.flush()
        return entry

    def _entry_is_past(self, entry: MeetingRegistryEntry, *, now: datetime) -> bool:
        if entry.slot_end is not None:
            return entry.slot_end < now
        if entry.series_occurrence_date is not None:
            return entry.series_occurrence_date < now.date()
        return False

    def _entry_needs_slot_update(
        self,
        entry: MeetingRegistryEntry,
        occurrence: SeriesOccurrence,
    ) -> bool:
        return (
            entry.series_occurrence_date != occurrence.occurrence_date
            or entry.slot_start != occurrence.slot_start
            or entry.slot_end != occurrence.slot_end
            or entry.outlook_item_id != occurrence.outlook_item_id
            or entry.subject != (occurrence.subject or entry.subject)
        )

    async def sync_series_card(self, series_id: uuid.UUID) -> SyncResult:
        meeting = await self._load_series(series_id)
        if meeting is None:
            return SyncResult(
                action="skipped",
                series_id=series_id,
                message="Серия не найдена",
            )
        if meeting.status != ScheduledMeetingStatus.PLANNED:
            return SyncResult(
                action="skipped",
                series_id=series_id,
                message=f"Серия в статусе {meeting.status.value}",
            )
        if not meeting.outlook_series_id:
            return SyncResult(
                action="skipped",
                series_id=series_id,
                message="Серия ещё не распланирована в Outlook",
            )

        occurrences, sync_source = await self._resolve_occurrences(meeting)
        if not occurrences:
            return SyncResult(
                action="no_occurrences",
                series_id=series_id,
                sync_source=sync_source,
                message="Не найдены вхождения серии",
            )

        registry = MeetingRegistryService(self.db)
        entry = await registry.get_entry_by_scheduled_meeting_id(meeting.id)
        now = self._now()

        if entry is None:
            target = find_next_occurrence(occurrences, now=now)
            if target is None:
                return SyncResult(
                    action="no_occurrences",
                    series_id=series_id,
                    sync_source=sync_source,
                    message="Нет будущих вхождений",
                )
            created = await self._create_entry(meeting, target, registry=registry)
            await registry.refresh_protocol_draft_schedule_for_entry(created)
            return SyncResult(
                action="created",
                series_id=series_id,
                entry_id=created.id,
                occurrence_date=target.occurrence_date,
                sync_source=sync_source,
            )

        if entry.stage == MeetingRegistryStage.CANCELLED:
            return SyncResult(
                action="skipped",
                series_id=series_id,
                entry_id=entry.id,
                message="Карточка отменена",
            )

        if self._entry_is_past(entry, now=now):
            previous_date = entry.series_occurrence_date
            previous_slot_start = entry.slot_start.isoformat() if entry.slot_start else None
            previous_slot_end = entry.slot_end.isoformat() if entry.slot_end else None
            target = (
                find_next_after(occurrences, after_date=previous_date)
                if previous_date is not None
                else find_next_occurrence(occurrences, now=now)
            )
            if target is None:
                return SyncResult(
                    action="no_occurrences",
                    series_id=series_id,
                    entry_id=entry.id,
                    sync_source=sync_source,
                    message="Серия завершена, следующих вхождений нет",
                )
            self._apply_occurrence_to_entry(entry, meeting, target)
            if entry.stage != MeetingRegistryStage.CANCELLED:
                entry.stage = MeetingRegistryStage.SCHEDULED
            await registry.append_event(
                entry,
                event_type=MeetingRegistryEventType.OCCURRENCE_ROLLED,
                message=(
                    f"Карточка переключена на {format_slot_label(target.slot_start.isoformat(), target.slot_end.isoformat())}"
                ),
                payload={
                    "from_date": previous_date.isoformat() if previous_date else None,
                    "to_date": target.occurrence_date.isoformat(),
                    "from_slot_start": previous_slot_start,
                    "from_slot_end": previous_slot_end,
                    "to_slot_start": target.slot_start.isoformat(),
                    "to_slot_end": target.slot_end.isoformat(),
                    "sync_source": sync_source,
                },
            )
            await self.db.flush()
            await registry.recreate_protocol_draft_on_reschedule(entry)
            return SyncResult(
                action="rolled",
                series_id=series_id,
                entry_id=entry.id,
                occurrence_date=target.occurrence_date,
                sync_source=sync_source,
            )

        current_date = entry.series_occurrence_date
        target = (
            find_occurrence_on_date(occurrences, occurrence_date=current_date)
            if current_date is not None
            else None
        ) or find_next_occurrence(occurrences, now=now)
        if target is None:
            return SyncResult(
                action="no_occurrences",
                series_id=series_id,
                entry_id=entry.id,
                sync_source=sync_source,
            )
        if not self._entry_needs_slot_update(entry, target):
            return SyncResult(
                action="skipped",
                series_id=series_id,
                entry_id=entry.id,
                occurrence_date=target.occurrence_date,
                sync_source=sync_source,
            )

        self._apply_occurrence_to_entry(entry, meeting, target)
        await registry.append_event(
            entry,
            event_type=MeetingRegistryEventType.RESCHEDULED,
            message=(
                f"Слот серии синхронизирован: {format_slot_label(target.slot_start.isoformat(), target.slot_end.isoformat())}"
            ),
            payload={
                "occurrence_date": target.occurrence_date.isoformat(),
                "sync_source": sync_source,
            },
        )
        await self.db.flush()
        await registry.recreate_protocol_draft_on_reschedule(entry)
        return SyncResult(
            action="updated",
            series_id=series_id,
            entry_id=entry.id,
            occurrence_date=target.occurrence_date,
            sync_source=sync_source,
        )

    async def sync_all_due_series(self) -> BatchSyncResult:
        result = await self.db.execute(
            select(ScheduledMeeting.id).where(
                ScheduledMeeting.status == ScheduledMeetingStatus.PLANNED,
                ScheduledMeeting.outlook_series_id.is_not(None),
            )
        )
        series_ids = list(result.scalars().all())
        created = rolled = updated = skipped = no_occurrences = 0
        errors: list[str] = []

        for series_id in series_ids:
            try:
                sync_result = await self.sync_series_card(series_id)
                if sync_result.action == "created":
                    created += 1
                elif sync_result.action == "rolled":
                    rolled += 1
                elif sync_result.action == "updated":
                    updated += 1
                elif sync_result.action == "no_occurrences":
                    no_occurrences += 1
                else:
                    skipped += 1
            except Exception as exc:
                logger.exception("scheduled_series_registry_sync_failed series_id=%s", series_id)
                errors.append(f"{series_id}: {exc}")

        return BatchSyncResult(
            processed=len(series_ids),
            created=created,
            rolled=rolled,
            updated=updated,
            skipped=skipped,
            no_occurrences=no_occurrences,
            errors=errors,
        )
