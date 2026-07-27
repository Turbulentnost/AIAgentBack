"""Опрос TurboProject: уведомления о новых проектах и создание серии РГ по предложению."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.meeting_redis_ops import meeting_redis_get, meeting_redis_setex
from app.models.app_notification import AppNotification
from app.models.enums import (
    AppNotificationType,
    ScheduledMeetingFrequency,
    ScheduledMeetingStatus,
    ScheduledMeetingType,
    ScheduledMeetingWeekday,
)
from app.models.meeting_category import MeetingCategory
from app.models.scheduled_meeting import ScheduledMeeting
from app.schemas.app_notification import (
    TurboProjectRgParticipantProposal,
    TurboProjectRgSeriesProposal,
    TurboProjectRgWeeklySlotProposal,
)
from app.schemas.scheduled_meeting import (
    ScheduledMeetingCreate,
    ScheduledMeetingParticipantCreate,
    ScheduledMeetingRead,
    ScheduledMeetingRecurrencePayload,
)
from app.services.meeting_constants import (
    DEFAULT_DURATION_MINUTES,
    QUORUM_MIN_COVERAGE_RATIO,
)
from app.services.meeting_permission import list_meeting_agent_users
from app.services.scheduled_meeting_person import (
    ResolvedPerson,
    ScheduledMeetingPersonError,
    resolve_person_by_fio,
)
from app.services.scheduled_meeting_recurrence import (
    WEEKDAY_TO_ISO,
    RecurrenceInput,
    default_series_end_date,
    format_recurrence_label,
)
from app.services.scheduled_meeting_service import (
    ScheduledMeetingService,
    ScheduledMeetingServiceError,
)
from app.tools.TurboProject.connection import TurboProjectError
from app.tools.TurboProject.projects import list_turbo_projects, parse_iso_date
from app.tools.TurboProject.working_group import get_turbo_project_working_group

logger = logging.getLogger(__name__)

RG_CATEGORY_NAME = "РГ по проекту"
MANAGER_ROLE = "Руководитель проекта"
RESPONSIBLE_ROLE = "Куратор"
TITLE_PREFIX = "РГ: "
IN_WORK_STATUSES = frozenset({"вработе", "в работе", "в_работе"})
ISO_TO_WEEKDAY = {iso: weekday for weekday, iso in WEEKDAY_TO_ISO.items()}
FALLBACK_WEEKDAY = ScheduledMeetingWeekday.MONDAY
FALLBACK_TIME = time(10, 0)
SLOT_SEARCH_MAX_DAYS = 30
DAILY_CANDIDATES_CACHE_PREFIX = "turbo_project:discover:candidates"
_memory_daily_candidates: dict[str, list[dict[str, Any]]] = {}


class TurboProjectSeriesSyncError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class WeeklySlotChoice:
    weekday: ScheduledMeetingWeekday
    time_local: time
    duration_minutes: int
    slot_start: str | None
    coverage_ratio: float | None
    fallback: bool


@dataclass
class TurboProjectDiscoverResult:
    candidates: int = 0
    notified: int = 0
    notifications_created: int = 0
    skipped_existing_series: int = 0
    skipped_already_notified: int = 0
    skipped_below_watermark: int = 0
    skipped_stale_upload: int = 0
    skipped_not_in_work: int = 0
    failed: int = 0
    cache_hit: bool = False
    items: list[dict[str, Any]] = field(default_factory=list)
    failed_items: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidates": self.candidates,
            "notified": self.notified,
            "notifications_created": self.notifications_created,
            "skipped_existing_series": self.skipped_existing_series,
            "skipped_already_notified": self.skipped_already_notified,
            "skipped_below_watermark": self.skipped_below_watermark,
            "skipped_stale_upload": self.skipped_stale_upload,
            "skipped_not_in_work": self.skipped_not_in_work,
            "failed": self.failed,
            "cache_hit": self.cache_hit,
            "items": self.items,
            "failed_items": self.failed_items,
        }


def turbo_project_entity_key(file_id: int) -> str:
    return f"turbo_project:{int(file_id)}"


def clear_daily_candidates_memory_cache() -> None:
    _memory_daily_candidates.clear()


class TurboProjectSeriesSyncService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def discover_and_notify(
        self,
        *,
        min_file_id: int | None = None,
        uploaded_within_days: int | None = None,
        force_refresh: bool = False,
    ) -> TurboProjectDiscoverResult:
        """Опрос: новые file_id (≥ watermark) → in-app уведомления (без 1С, с дневным кэшем)."""
        watermark = (
            settings.TURBO_PROJECT_SERIES_MIN_FILE_ID
            if min_file_id is None
            else int(min_file_id)
        )
        lookback_days = (
            settings.TURBO_PROJECT_SERIES_UPLOADED_WITHIN_DAYS
            if uploaded_within_days is None
            else int(uploaded_within_days)
        )
        # 0 = не фильтровать по uploaded_at (в TP это часто дата обновления, не создания).
        lookback_days = max(int(lookback_days), 0)
        today = self._local_today()
        min_upload_date = (
            today - timedelta(days=lookback_days - 1) if lookback_days > 0 else None
        )

        result = TurboProjectDiscoverResult()
        cache_day_key = f"{today.isoformat()}:min={watermark}:days={lookback_days}"
        candidates: list[dict[str, Any]] | None = None
        if not force_refresh:
            candidates = await self._load_daily_candidates_cache(cache_day_key)
            if candidates is not None:
                result.cache_hit = True
                logger.info(
                    "turbo_project_discover.cache_hit key=%s candidates=%s",
                    cache_day_key,
                    len(candidates),
                )

        if candidates is None:
            try:
                listing = list_turbo_projects(
                    only_with_1c=False,
                    include_details=False,
                )
            except TurboProjectError as exc:
                raise TurboProjectSeriesSyncError(str(exc), status_code=503) from exc

            projects = listing.get("projects") or []
            if not isinstance(projects, list):
                raise TurboProjectSeriesSyncError(
                    "TurboProject вернул некорректный список проектов",
                    status_code=502,
                )

            candidates = []
            for item in projects:
                if not isinstance(item, dict):
                    continue
                file_id = self._project_file_id(item)
                if file_id is None:
                    continue
                if watermark > 0 and file_id < watermark:
                    result.skipped_below_watermark += 1
                    continue
                if min_upload_date is not None:
                    upload_date = self._uploaded_local_date(item.get("uploaded_at"))
                    if upload_date is None or upload_date < min_upload_date:
                        result.skipped_stale_upload += 1
                        continue
                candidates.append(
                    {
                        "file_id": file_id,
                        "project_name": item.get("project_name") or item.get("original_name"),
                        "original_name": item.get("original_name"),
                        "uploaded_at": item.get("uploaded_at"),
                        "has_1c": bool(item.get("has_1c")),
                    }
                )
            candidates.sort(key=lambda item: int(item.get("file_id") or 0))
            await self._store_daily_candidates_cache(cache_day_key, candidates)

        result.candidates = len(candidates)

        recipients = await list_meeting_agent_users(self.db)
        if not recipients:
            logger.warning("turbo_project_discover.no_meeting_agent_recipients")

        for summary in candidates:
            file_id = self._project_file_id(summary)
            assert file_id is not None
            try:
                outcome = await self._notify_one_project(
                    file_id=file_id,
                    summary=summary,
                    recipients=recipients,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "turbo_project_discover.failed file_id=%s error=%s",
                    file_id,
                    exc,
                )
                result.failed += 1
                result.failed_items.append(
                    {
                        "file_id": file_id,
                        "project_name": summary.get("project_name"),
                        "error": str(exc),
                    }
                )
                continue

            status = outcome.get("status")
            if status == "skipped_existing_series":
                result.skipped_existing_series += 1
            elif status == "skipped_already_notified":
                result.skipped_already_notified += 1
            elif status == "skipped_not_in_work":
                result.skipped_not_in_work += 1
            elif status == "notified":
                result.notified += 1
                result.notifications_created += int(outcome.get("notifications_created") or 0)
                result.items.append(outcome)
            else:
                result.failed += 1
                result.failed_items.append(outcome)

        return result

    async def build_series_proposal(self, file_id: int) -> TurboProjectRgSeriesProposal:
        """Outlook + quorum: предложение серии для конкретного проекта."""
        try:
            working_group = get_turbo_project_working_group(file_id=file_id)
        except (TurboProjectError, ValueError) as exc:
            raise TurboProjectSeriesSyncError(str(exc), status_code=502) from exc

        project_meta = (
            working_group.get("project")
            if isinstance(working_group.get("project"), dict)
            else {}
        )
        data_1c = (
            project_meta.get("data_1c")
            if isinstance(project_meta.get("data_1c"), dict)
            else {}
        )
        project_name = (
            str(working_group.get("project_name") or "").strip() or f"Проект {file_id}"
        )
        # Статус 1С пока не фильтруем — отбор только по uploaded_at / дедупу.
        status_raw = data_1c.get("status_proekta")

        series_start, series_end = self._resolve_series_dates(project_meta)
        if series_start is None or series_end is None:
            raise TurboProjectSeriesSyncError(
                "Не удалось определить даты начала/окончания проекта",
                status_code=422,
            )
        if series_end < series_start:
            raise TurboProjectSeriesSyncError(
                f"Дата окончания проекта ({series_end}) раньше даты начала ({series_start})",
                status_code=422,
            )

        members = working_group.get("members") or []
        if not isinstance(members, list):
            members = []
        resolved_members, member_roles = await self._resolve_members_with_roles(members)
        if not resolved_members:
            raise TurboProjectSeriesSyncError(
                "Нет участников с корпоративной почтой Outlook",
                status_code=422,
            )

        manager_person, responsible_person = self._pick_manager_and_responsible(
            members,
            resolved_members,
        )
        slot = await self._pick_weekly_slot(
            attendee_emails=[p.email for p in resolved_members if p.email],
            required_emails=[manager_person.email, responsible_person.email],
            series_start=series_start,
            series_end=series_end,
            duration_minutes=DEFAULT_DURATION_MINUTES,
        )
        recurrence_label = format_recurrence_label(
            RecurrenceInput(
                frequency=ScheduledMeetingFrequency.WEEKLY,
                interval=1,
                time_local=slot.time_local,
                duration_minutes=slot.duration_minutes,
                series_start_date=series_start,
                series_end_date=series_end,
                weekday=slot.weekday,
            )
        )
        title = self._build_title(project_name)
        one_c_ref_key = str(working_group.get("one_c_ref_key") or "").strip() or None

        def to_participant(person: ResolvedPerson) -> TurboProjectRgParticipantProposal:
            return TurboProjectRgParticipantProposal(
                user_id=person.user_id,
                fio=person.fio,
                email=person.email,
                role=member_roles.get(person.user_id),
                position_name=person.position_name,
            )

        participants = [
            to_participant(person)
            for person in resolved_members
            if person.user_id not in {manager_person.user_id, responsible_person.user_id}
        ]

        return TurboProjectRgSeriesProposal(
            file_id=file_id,
            project_name=project_name,
            one_c_ref_key=one_c_ref_key,
            nomer_proekta=str(data_1c.get("nomer_proekta") or "") or None,
            status_proekta=str(status_raw) if status_raw else None,
            title=title,
            meeting_category_name=RG_CATEGORY_NAME,
            series_start_date=series_start,
            series_end_date=series_end,
            recurrence_label=recurrence_label,
            weekly_slot=TurboProjectRgWeeklySlotProposal(
                weekday=slot.weekday,
                time_local=slot.time_local,
                duration_minutes=slot.duration_minutes,
                slot_start=slot.slot_start,
                coverage_ratio=slot.coverage_ratio,
                fallback=slot.fallback,
            ),
            manager=to_participant(manager_person),
            responsible=to_participant(responsible_person),
            participants=participants,
        )

    async def create_series_from_proposal(
        self,
        proposal: TurboProjectRgSeriesProposal,
        *,
        weekday: ScheduledMeetingWeekday | None = None,
        time_local: time | None = None,
        duration_minutes: int | None = None,
    ) -> ScheduledMeetingRead:
        existing = await self._find_existing_series(
            file_id=proposal.file_id,
            one_c_ref_key=proposal.one_c_ref_key,
        )
        if existing is not None:
            return ScheduledMeetingService(self.db).to_read(existing)

        category = await self._resolve_rg_category()
        slot_weekday = weekday or proposal.weekly_slot.weekday
        slot_time = time_local or proposal.weekly_slot.time_local
        slot_duration = duration_minutes or proposal.weekly_slot.duration_minutes

        recurrence_label = format_recurrence_label(
            RecurrenceInput(
                frequency=ScheduledMeetingFrequency.WEEKLY,
                interval=1,
                time_local=slot_time,
                duration_minutes=slot_duration,
                series_start_date=proposal.series_start_date,
                series_end_date=proposal.series_end_date,
                weekday=slot_weekday,
            )
        )
        participants = [
            ScheduledMeetingParticipantCreate(
                user_id=item.user_id,
                person_fio=item.fio,
                person_email=item.email,
                sort_order=index,
            )
            for index, item in enumerate(proposal.participants)
        ]
        create_payload = ScheduledMeetingCreate(
            title=proposal.title,
            meeting_category_id=category.id,
            manager_user_id=proposal.manager.user_id,
            responsible_user_id=proposal.responsible.user_id,
            meeting_type=ScheduledMeetingType.PLANNED,
            status=ScheduledMeetingStatus.CREATED,
            series_start_date=proposal.series_start_date,
            series_end_date=proposal.series_end_date,
            recurrence_label=recurrence_label,
            recurrence=ScheduledMeetingRecurrencePayload(
                frequency=ScheduledMeetingFrequency.WEEKLY,
                interval=1,
                time_local=slot_time,
                duration_minutes=slot_duration,
                weekday=slot_weekday,
                series_start_date=proposal.series_start_date,
                series_end_date=proposal.series_end_date,
            ),
            participants=participants,
            payload={
                "source": "turbo_project",
                "turbo_project_file_id": proposal.file_id,
                "one_c_ref_key": proposal.one_c_ref_key,
                "project_name": proposal.project_name,
                "nomer_proekta": proposal.nomer_proekta,
                "status_proekta": proposal.status_proekta,
                "weekly_slot": {
                    "weekday": slot_weekday.value,
                    "time_local": slot_time.strftime("%H:%M"),
                    "slot_start": proposal.weekly_slot.slot_start,
                    "coverage_ratio": proposal.weekly_slot.coverage_ratio,
                    "fallback": proposal.weekly_slot.fallback,
                },
            },
        )
        try:
            return await ScheduledMeetingService(self.db).create(create_payload)
        except ScheduledMeetingServiceError as exc:
            raise TurboProjectSeriesSyncError(str(exc), status_code=exc.status_code) from exc

    async def _notify_one_project(
        self,
        *,
        file_id: int,
        summary: dict[str, Any],
        recipients: list[Any],
    ) -> dict[str, Any]:
        entity_key = turbo_project_entity_key(file_id)
        existing_series = await self._find_existing_series(file_id=file_id, one_c_ref_key=None)
        if existing_series is not None:
            return {
                "status": "skipped_existing_series",
                "file_id": file_id,
                "project_name": summary.get("project_name"),
                "scheduled_meeting_id": str(existing_series.id),
            }

        if await self._entity_key_exists(entity_key):
            return {
                "status": "skipped_already_notified",
                "file_id": file_id,
                "project_name": summary.get("project_name"),
                "entity_key": entity_key,
            }

        # Пока без 1С/деталей проекта: уведомляем по данным списка TurboProject.
        project_name = (
            str(summary.get("project_name") or summary.get("original_name") or "").strip()
            or f"Проект {file_id}"
        )
        title = "Новый проект: нужна рабочая группа"
        body = (
            f"По проекту «{project_name}» нужно создать серию совещаний «РГ по проекту»."
        )
        payload = {
            "file_id": file_id,
            "project_name": project_name,
            "one_c_ref_key": None,
            "nomer_proekta": None,
            "status_proekta": None,
            "uploaded_at": summary.get("uploaded_at"),
        }

        created = 0
        for user in recipients:
            exists = await self.db.scalar(
                select(AppNotification.id).where(
                    AppNotification.user_id == user.id,
                    AppNotification.entity_key == entity_key,
                )
            )
            if exists is not None:
                continue
            self.db.add(
                AppNotification(
                    user_id=user.id,
                    type=AppNotificationType.TURBO_PROJECT_RG.value,
                    title=title,
                    body=body,
                    entity_key=entity_key,
                    payload=payload,
                )
            )
            created += 1

        if created:
            await self.db.flush()

        return {
            "status": "notified",
            "file_id": file_id,
            "project_name": project_name,
            "entity_key": entity_key,
            "notifications_created": created,
            "recipients": len(recipients),
        }

    async def _entity_key_exists(self, entity_key: str) -> bool:
        found = await self.db.scalar(
            select(AppNotification.id).where(AppNotification.entity_key == entity_key).limit(1)
        )
        return found is not None

    async def _resolve_members_with_roles(
        self,
        members: list[Any],
    ) -> tuple[list[ResolvedPerson], dict[uuid.UUID, str]]:
        resolved: list[ResolvedPerson] = []
        roles: dict[uuid.UUID, str] = {}
        seen: set[uuid.UUID] = set()
        for member in members:
            if not isinstance(member, dict):
                continue
            fio = str(member.get("fio") or "").strip()
            if not fio:
                continue
            try:
                person = await resolve_person_by_fio(self.db, fio)
            except ScheduledMeetingPersonError as exc:
                logger.info(
                    "turbo_project_series_sync.skip_member_without_outlook fio=%s error=%s",
                    fio,
                    exc,
                )
                continue
            if person.user_id in seen:
                continue
            seen.add(person.user_id)
            resolved.append(person)
            role = str(member.get("role") or "").strip()
            if role:
                roles[person.user_id] = role
        return resolved, roles

    def _pick_manager_and_responsible(
        self,
        members: list[Any],
        resolved: list[ResolvedPerson],
    ) -> tuple[ResolvedPerson, ResolvedPerson]:
        by_fio = {person.fio.casefold(): person for person in resolved}
        manager_member = self._find_member_by_role(members, MANAGER_ROLE)
        responsible_member = self._find_member_by_role(members, RESPONSIBLE_ROLE)

        manager = None
        if manager_member is not None:
            manager = by_fio.get(manager_member["fio"].casefold())
        if manager is None:
            manager = resolved[0]

        responsible = None
        if responsible_member is not None:
            responsible = by_fio.get(responsible_member["fio"].casefold())
        if responsible is None:
            responsible = manager
        return manager, responsible

    async def _pick_weekly_slot(
        self,
        *,
        attendee_emails: list[str],
        required_emails: list[str],
        series_start: date,
        series_end: date,
        duration_minutes: int,
    ) -> WeeklySlotChoice:
        unique_emails: list[str] = []
        seen: set[str] = set()
        for email in attendee_emails:
            normalized = (email or "").strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique_emails.append(normalized)
        if not unique_emails:
            return WeeklySlotChoice(
                weekday=FALLBACK_WEEKDAY,
                time_local=FALLBACK_TIME,
                duration_minutes=duration_minutes,
                slot_start=None,
                coverage_ratio=None,
                fallback=True,
            )

        required: list[str] = []
        for email in required_emails:
            normalized = (email or "").strip().lower()
            if normalized and normalized in seen and normalized not in required:
                required.append(normalized)

        tz_name = (settings.OUTLOOK_TIMEZONE or "Europe/Moscow").strip() or "Europe/Moscow"
        tz = ZoneInfo(tz_name)
        search_from = max(series_start, date.today())
        preferred_dt = datetime.combine(search_from, time(9, 0), tzinfo=tz)
        latest_dt = datetime.combine(series_end, time(17, 0), tzinfo=tz)
        if latest_dt <= preferred_dt:
            return WeeklySlotChoice(
                weekday=FALLBACK_WEEKDAY,
                time_local=FALLBACK_TIME,
                duration_minutes=duration_minutes,
                slot_start=None,
                coverage_ratio=None,
                fallback=True,
            )

        max_days = min(
            SLOT_SEARCH_MAX_DAYS,
            max((series_end - search_from).days + 1, 1),
        )
        try:
            from app.tools.Outlook.slot_search.api import dispatch_find_quorum_meeting_slots

            result = await asyncio.to_thread(
                dispatch_find_quorum_meeting_slots,
                attendees=unique_emails,
                preferred=preferred_dt.strftime("%Y-%m-%d %H:%M"),
                duration_minutes=duration_minutes,
                required_attendees=required or None,
                min_coverage_ratio=QUORUM_MIN_COVERAGE_RATIO,
                max_results=1,
                max_days=max_days,
                verify_calendar=True,
                quiet=True,
                latest_allowed=latest_dt.strftime("%Y-%m-%d %H:%M"),
                raise_if_empty=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("turbo_project_series_sync.slot_search_failed error=%s", exc)
            return WeeklySlotChoice(
                weekday=FALLBACK_WEEKDAY,
                time_local=FALLBACK_TIME,
                duration_minutes=duration_minutes,
                slot_start=None,
                coverage_ratio=None,
                fallback=True,
            )

        candidates = result.get("candidates") if isinstance(result, dict) else None
        if not candidates:
            return WeeklySlotChoice(
                weekday=FALLBACK_WEEKDAY,
                time_local=FALLBACK_TIME,
                duration_minutes=duration_minutes,
                slot_start=None,
                coverage_ratio=None,
                fallback=True,
            )

        best = candidates[0]
        slot_start_raw = best.get("slot_start")
        slot_dt = self._parse_slot_datetime(slot_start_raw, tz)
        if slot_dt is None:
            return WeeklySlotChoice(
                weekday=FALLBACK_WEEKDAY,
                time_local=FALLBACK_TIME,
                duration_minutes=duration_minutes,
                slot_start=str(slot_start_raw) if slot_start_raw else None,
                coverage_ratio=self._as_float(best.get("coverage_ratio")),
                fallback=True,
            )

        local_dt = slot_dt.astimezone(tz)
        weekday = ISO_TO_WEEKDAY.get(local_dt.weekday(), FALLBACK_WEEKDAY)
        return WeeklySlotChoice(
            weekday=weekday,
            time_local=local_dt.time().replace(microsecond=0),
            duration_minutes=duration_minutes,
            slot_start=local_dt.isoformat(),
            coverage_ratio=self._as_float(best.get("coverage_ratio")),
            fallback=False,
        )

    async def _find_existing_series(
        self,
        *,
        file_id: int | None,
        one_c_ref_key: str | None,
    ) -> ScheduledMeeting | None:
        if file_id is not None:
            result = await self.db.execute(
                select(ScheduledMeeting)
                .where(
                    ScheduledMeeting.payload["turbo_project_file_id"].astext
                    == str(int(file_id))
                )
                .limit(1)
            )
            found = result.scalar_one_or_none()
            if found is not None:
                return found

        if one_c_ref_key:
            result = await self.db.execute(
                select(ScheduledMeeting)
                .where(
                    ScheduledMeeting.payload["source"].astext == "turbo_project",
                    ScheduledMeeting.payload["one_c_ref_key"].astext == one_c_ref_key,
                )
                .limit(1)
            )
            return result.scalar_one_or_none()
        return None

    async def _resolve_rg_category(self) -> MeetingCategory:
        result = await self.db.execute(
            select(MeetingCategory)
            .where(
                MeetingCategory.is_active.is_(True),
                MeetingCategory.name == RG_CATEGORY_NAME,
            )
            .limit(1)
        )
        category = result.scalar_one_or_none()
        if category is not None:
            return category
        raise TurboProjectSeriesSyncError(
            f"Категория «{RG_CATEGORY_NAME}» не найдена в справочнике графика",
            status_code=503,
        )

    @staticmethod
    def _project_file_id(project: dict[str, Any]) -> int | None:
        raw = project.get("file_id")
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _local_tz() -> ZoneInfo:
        tz_name = (settings.OUTLOOK_TIMEZONE or "Europe/Moscow").strip() or "Europe/Moscow"
        return ZoneInfo(tz_name)

    @classmethod
    def _local_today(cls) -> date:
        return datetime.now(cls._local_tz()).date()

    @classmethod
    def _daily_candidates_cache_key(cls, cache_key: str) -> str:
        return f"{DAILY_CANDIDATES_CACHE_PREFIX}:{cache_key}"

    @classmethod
    def _seconds_until_local_midnight(cls) -> int:
        tz = cls._local_tz()
        now = datetime.now(tz)
        tomorrow = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=tz)
        return max(int((tomorrow - now).total_seconds()), 60)

    async def _load_daily_candidates_cache(self, cache_key: str) -> list[dict[str, Any]] | None:
        key = self._daily_candidates_cache_key(cache_key)
        cached = _memory_daily_candidates.get(key)
        if cached is not None:
            return list(cached)

        try:
            raw = await meeting_redis_get(key)
        except Exception as exc:  # noqa: BLE001
            logger.debug("turbo_project_discover.cache_redis_unavailable error=%s", exc)
            return None
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("key") != cache_key:
            return None
        items = payload.get("candidates")
        if not isinstance(items, list):
            return None
        candidates = [item for item in items if isinstance(item, dict)]
        _memory_daily_candidates[key] = list(candidates)
        return candidates

    async def _store_daily_candidates_cache(
        self,
        cache_key: str,
        candidates: list[dict[str, Any]],
    ) -> None:
        key = self._daily_candidates_cache_key(cache_key)
        snapshot = list(candidates)
        _memory_daily_candidates[key] = snapshot
        payload = json.dumps(
            {"key": cache_key, "candidates": snapshot},
            ensure_ascii=False,
            default=str,
        )
        try:
            await meeting_redis_setex(key, self._seconds_until_local_midnight(), payload)
        except Exception as exc:  # noqa: BLE001
            logger.debug("turbo_project_discover.cache_redis_set_failed error=%s", exc)

    @classmethod
    def _uploaded_local_date(cls, value: Any) -> date | None:
        uploaded = parse_iso_date(value)
        if uploaded is None:
            return None
        tz = cls._local_tz()
        if uploaded.tzinfo is None:
            uploaded = uploaded.replace(tzinfo=tz)
        return uploaded.astimezone(tz).date()

    @staticmethod
    def _is_in_work_status(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        normalized = (
            value.strip()
            .casefold()
            .replace("ё", "е")
            .replace("_", " ")
            .replace("-", " ")
        )
        normalized = " ".join(normalized.split())
        compact = normalized.replace(" ", "")
        return compact in IN_WORK_STATUSES or normalized in IN_WORK_STATUSES

    @classmethod
    def _resolve_series_dates(
        cls,
        project_meta: dict[str, Any],
    ) -> tuple[date | None, date | None]:
        dates = project_meta.get("dates") if isinstance(project_meta.get("dates"), dict) else {}
        data_1c = (
            project_meta.get("data_1c")
            if isinstance(project_meta.get("data_1c"), dict)
            else {}
        )
        start = cls._to_date(
            dates.get("start_date")
            or data_1c.get("planovaya_data_nachala")
            or data_1c.get("data_nachala")
        )
        end = cls._to_date(
            dates.get("finish_date")
            or data_1c.get("planovaya_data_okonchaniya")
            or data_1c.get("data_okonchaniya")
            or dates.get("plan_finish_1c")
        )
        if start is None:
            start = date.today()
        if end is None:
            end = default_series_end_date(year=start.year)
        return start, end

    @staticmethod
    def _to_date(value: Any) -> date | None:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        parsed = parse_iso_date(value)
        if parsed is None:
            return None
        return parsed.date()

    @staticmethod
    def _parse_slot_datetime(value: Any, tz: ZoneInfo) -> datetime | None:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=tz)
            return value
        if not isinstance(value, str) or not value.strip():
            return None
        raw = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=tz)
        return parsed

    @staticmethod
    def _as_float(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _find_member_by_role(members: list[Any], role: str) -> dict[str, str] | None:
        for member in members:
            if not isinstance(member, dict):
                continue
            if str(member.get("role") or "") == role and str(member.get("fio") or "").strip():
                return {
                    "fio": str(member["fio"]).strip(),
                    "role": role,
                    "source": str(member.get("source") or ""),
                }
        return None

    @staticmethod
    def _build_title(project_name: str) -> str:
        cleaned = project_name.strip() or "Проект"
        title = f"{TITLE_PREFIX}{cleaned}"
        if len(title) <= 512:
            return title
        return title[:509].rstrip() + "..."
