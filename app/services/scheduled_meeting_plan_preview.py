from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.enums import ScheduledMeetingStatus, ScheduledMeetingWeekday
from app.models.scheduled_meeting import ScheduledMeeting
from app.schemas.scheduled_meeting import (
    ScheduledMeetingPlanConflictRead,
    ScheduledMeetingPlanOccurrencePreview,
    ScheduledMeetingPlanPreviewRead,
)
from app.services.meeting_constants import (
    RESCHEDULE_HINT_SEARCH_DAYS,
    SLOT_PREVIEW_TIMEOUT_SECONDS,
)
from app.services.scheduled_meeting_occurrences import build_occurrences_from_rule
from app.services.scheduled_meeting_outlook import (
    ScheduledMeetingOutlookError,
    resolve_attendee_emails,
)
from app.services.scheduled_meeting_plan_costs import build_conflict_options
from app.tools.Outlook.cancel_meeting import to_local
from app.tools.Outlook.send_meeting_invite import load_config
from app.tools.Outlook.slot_search.availability import (
    is_free_for_all,
    partition_attendees_at_slot,
)
from app.tools.Outlook.slot_search.busy import busy_intervals_and_events_from_freebusy
from app.tools.Outlook.slot_search.conflicts import (
    attach_reschedule_hints,
    conflicting_events_at_slot,
    conflicting_intervals_at_slot,
)
from app.tools.Outlook.slot_search.constants import FORBIDDEN_BLOCKS, WORK_END, WORK_START
from app.tools.Outlook.slot_search.rules import (
    combine,
    intervals_overlap,
    is_workday,
    slot_respects_rules,
)
from app.tools.Outlook.slot_search.search import find_quorum_slots

logger = logging.getLogger(__name__)

ConflictPolicy = Literal["strict", "soft_week", "skip"]
OccurrencePreviewStatus = Literal["ok", "conflict", "shifted", "skip", "unresolved"]
FREEBUSY_CHUNK_DAYS = 60


class ScheduledMeetingPlanPreviewError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def work_week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def work_week_friday(day: date) -> date:
    return work_week_monday(day) + timedelta(days=4)


def week_latest_allowed(occurrence_date: date, *, timezone_name: str) -> datetime:
    friday = work_week_friday(occurrence_date)
    return datetime.combine(friday, WORK_END, tzinfo=ZoneInfo(timezone_name))


def _normalize_emails(emails: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for email in emails:
        key = email.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def _format_slot_label(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def _parse_slot_datetime(value: str | datetime, *, timezone_name: str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=ZoneInfo(timezone_name))
        return value.astimezone(ZoneInfo(timezone_name))
    from app.tools.Outlook.send_meeting_invite import parse_start

    return parse_start(value, timezone_name)


def _normalize_conflict_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _build_rule_conflict_reads(
    *,
    slot_start: datetime,
    duration: timedelta,
    config: Any,
) -> list[ScheduledMeetingPlanConflictRead]:
    """Конфликты с правилами УД (рабочие часы, обед 12:00–13:00), не FreeBusy."""
    if slot_respects_rules(slot_start, duration, config):
        return []

    start = to_local(slot_start, config)
    end = start + duration
    subjects: list[str] = []

    if not is_workday(start, config):
        subjects.append("Нерабочий день")
    if start.date() != end.date():
        subjects.append("Слот пересекает сутки")
    if start.time() < WORK_START or end.time() > WORK_END:
        subjects.append(
            f"Вне рабочих часов {WORK_START.strftime('%H:%M')}–{WORK_END.strftime('%H:%M')}"
        )
    for block_start_t, block_end_t in FORBIDDEN_BLOCKS:
        block_start = combine(start, block_start_t, config)
        block_end = combine(start, block_end_t, config)
        if intervals_overlap(start, end, block_start, block_end):
            subjects.append(
                f"Обеденный перерыв {block_start_t.strftime('%H:%M')}–{block_end_t.strftime('%H:%M')}"
            )

    if not subjects:
        subjects.append("Слот не соответствует правилам УД")

    return [
        ScheduledMeetingPlanConflictRead(
            attendee_email="правила УД",
            event_start=_normalize_conflict_iso(start),
            event_end=_normalize_conflict_iso(end),
            event_subject=subject,
            busy_type="Busy",
            movability="low",
            source="rule",
        )
        for subject in subjects
    ]


def _build_conflict_reads(
    *,
    busy_attendees: list[str],
    slot_start: datetime,
    duration: timedelta,
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    events_by_attendee: dict[str, list[Any]],
    config: Any,
) -> list[ScheduledMeetingPlanConflictRead]:
    records: list[ScheduledMeetingPlanConflictRead] = []
    reserved_slot = (slot_start, slot_start + duration)
    search_end = slot_start + timedelta(days=RESCHEDULE_HINT_SEARCH_DAYS)

    for email in busy_attendees:
        events = events_by_attendee.get(email) or []
        raw_records: list[dict[str, Any]] = []
        if events:
            for item in conflicting_events_at_slot(events, slot_start, duration, config):
                raw_records.append({**item, "source": "freebusy"})
        if not raw_records:
            for item in conflicting_intervals_at_slot(
                busy_by_attendee.get(email, []),
                slot_start,
                duration,
                config,
            ):
                raw_records.append({**item, "source": "interval"})

        if not raw_records:
            continue

        hinted = attach_reschedule_hints(
            raw_records,
            owner_email=email,
            busy_intervals=busy_by_attendee.get(email, []),
            config=config,
            step=timedelta(minutes=15),
            search_end=search_end,
            reserved_slot=reserved_slot,
            light_hints=True,
        )
        for item in hinted:
            records.append(
                ScheduledMeetingPlanConflictRead(
                    attendee_email=email,
                    event_start=_normalize_conflict_iso(item.get("event_start")),
                    event_end=_normalize_conflict_iso(item.get("event_end")),
                    event_subject=item.get("event_subject"),
                    busy_type=item.get("busy_type"),
                    movability=item.get("movability"),
                    source=item.get("source") or "interval",
                    reschedule_hint_start=_normalize_conflict_iso(
                        item.get("reschedule_hint_start")
                    ),
                    reschedule_hint_end=_normalize_conflict_iso(
                        item.get("reschedule_hint_end")
                    ),
                )
            )
    return records


def _freebusy_window(
    occurrences: list[Any],
    *,
    timezone_name: str,
) -> tuple[datetime, datetime]:
    starts = [item.slot_start for item in occurrences]
    window_start = min(starts) - timedelta(hours=1)
    last_week_end = week_latest_allowed(max(starts).date(), timezone_name=timezone_name)
    # Path B hints may look up to RESCHEDULE_HINT_SEARCH_DAYS past last occurrence.
    hint_end = max(starts) + timedelta(days=RESCHEDULE_HINT_SEARCH_DAYS)
    window_end = max(
        last_week_end,
        max(item.slot_end for item in occurrences),
        hint_end,
    ) + timedelta(hours=1)
    return window_start, window_end


def _fetch_freebusy_chunked(
    config: Any,
    attendees: list[str],
    range_start: datetime,
    range_end: datetime,
) -> tuple[dict[str, list[tuple[datetime, datetime]]], dict[str, list[Any]]]:
    """Exchange ограничивает GetUserAvailability интервалом не более 62 дней."""
    busy_result: dict[str, list[tuple[datetime, datetime]]] = {}
    events_result: dict[str, list[Any]] = {}
    chunk_start = range_start

    while chunk_start < range_end:
        chunk_end = min(
            chunk_start + timedelta(days=FREEBUSY_CHUNK_DAYS),
            range_end,
        )
        busy_chunk, events_chunk = busy_intervals_and_events_from_freebusy(
            config,
            attendees,
            chunk_start,
            chunk_end,
        )
        for email, intervals in busy_chunk.items():
            busy_result.setdefault(email, []).extend(intervals)
        for email, events in events_chunk.items():
            events_result.setdefault(email, []).extend(events)
        chunk_start = chunk_end

    return busy_result, events_result


def _pick_fully_free_candidate(
    payload: dict[str, Any],
    *,
    planned_start: datetime,
    timezone_name: str,
) -> tuple[datetime, datetime] | None:
    for candidate in payload.get("candidates") or []:
        coverage = candidate.get("coverage") or {}
        if coverage.get("ratio", 0) < 1.0:
            continue
        if coverage.get("required_ok") is False:
            continue
        busy = candidate.get("busy_attendees") or []
        if busy:
            continue
        slot_start_raw = candidate.get("slot_start")
        slot_end_raw = candidate.get("slot_end")
        if not slot_start_raw or not slot_end_raw:
            continue
        slot_start = _parse_slot_datetime(slot_start_raw, timezone_name=timezone_name)
        slot_end = _parse_slot_datetime(slot_end_raw, timezone_name=timezone_name)
        if slot_start == planned_start:
            continue
        if work_week_monday(slot_start.date()) != work_week_monday(planned_start.date()):
            continue
        return slot_start, slot_end
    return None


def _find_soft_week_slot(
    *,
    config: Any,
    attendees: list[str],
    planned_start: datetime,
    duration: timedelta,
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
) -> tuple[datetime, datetime] | None:
    timezone_name = config.timezone
    latest_allowed = week_latest_allowed(planned_start.date(), timezone_name=timezone_name)
    if planned_start >= latest_allowed:
        return None
    days_left = max((latest_allowed.date() - planned_start.date()).days + 1, 1)
    payload = find_quorum_slots(
        config=config,
        attendees=attendees,
        preferred=planned_start,
        duration=duration,
        max_days=min(days_left, 7),
        step=timedelta(minutes=15),
        max_items=500,
        source="freebusy",
        workers=1,
        required_attendees=attendees,
        min_coverage_ratio=1.0,
        max_results=3,
        verify_top_n=0,
        verify_calendar=False,
        latest_allowed=latest_allowed,
        raise_if_empty=False,
        prefetched_busy_by_attendee=busy_by_attendee,
    )
    return _pick_fully_free_candidate(
        payload,
        planned_start=planned_start,
        timezone_name=timezone_name,
    )


def evaluate_occurrence_preview(
    *,
    occurrence: Any,
    conflict_policy: ConflictPolicy,
    attendees: list[str],
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    events_by_attendee: dict[str, list[Any]] | None = None,
    config: Any,
    anchor_weekday: ScheduledMeetingWeekday | None = None,
) -> ScheduledMeetingPlanOccurrencePreview:
    events_by_attendee = events_by_attendee or {}
    duration = occurrence.slot_end - occurrence.slot_start
    rule_conflicts = _build_rule_conflict_reads(
        slot_start=occurrence.slot_start,
        duration=duration,
        config=config,
    )
    free, busy = partition_attendees_at_slot(
        occurrence.slot_start,
        duration,
        attendees=attendees,
        busy_by_attendee=busy_by_attendee,
        config=config,
    )
    del free

    calendars_free = is_free_for_all(
        occurrence.slot_start, duration, busy_by_attendee, config
    )
    if not rule_conflicts and calendars_free:
        return ScheduledMeetingPlanOccurrencePreview(
            occurrence_date=occurrence.occurrence_date,
            planned_start=_format_slot_label(occurrence.slot_start),
            planned_end=_format_slot_label(occurrence.slot_end),
            status="ok",
            busy_attendees=[],
            conflicts=[],
            suggested_start=None,
            suggested_end=None,
            options=[],
            recommended_option=None,
        )

    conflicts = [
        *rule_conflicts,
        *_build_conflict_reads(
            busy_attendees=busy,
            slot_start=occurrence.slot_start,
            duration=duration,
            busy_by_attendee=busy_by_attendee,
            events_by_attendee=events_by_attendee,
            config=config,
        ),
    ]

    suggested_slot: tuple[datetime, datetime] | None = None
    if conflict_policy == "soft_week":
        suggested_slot = _find_soft_week_slot(
            config=config,
            attendees=attendees,
            planned_start=occurrence.slot_start,
            duration=duration,
            busy_by_attendee=busy_by_attendee,
        )

    options, recommended = build_conflict_options(
        policy=conflict_policy,
        planned_start=occurrence.slot_start,
        suggested_slot=suggested_slot,
        conflicts=conflicts,
        anchor_weekday=anchor_weekday,
        format_slot=_format_slot_label,
    )

    suggested_start = None
    suggested_end = None
    if suggested_slot is not None:
        suggested_start = _format_slot_label(suggested_slot[0])
        suggested_end = _format_slot_label(suggested_slot[1])

    if conflict_policy == "strict":
        status: OccurrencePreviewStatus = "conflict"
    elif conflict_policy == "skip":
        status = "skip"
    elif suggested_slot is not None:
        status = "shifted"
    else:
        status = "unresolved"

    return ScheduledMeetingPlanOccurrencePreview(
        occurrence_date=occurrence.occurrence_date,
        planned_start=_format_slot_label(occurrence.slot_start),
        planned_end=_format_slot_label(occurrence.slot_end),
        status=status,
        busy_attendees=busy,
        conflicts=conflicts,
        suggested_start=suggested_start,
        suggested_end=suggested_end,
        options=options,
        recommended_option=recommended,
    )


async def build_plan_preview(
    db: AsyncSession,
    meeting: ScheduledMeeting,
    *,
    conflict_policy: ConflictPolicy = "soft_week",
) -> ScheduledMeetingPlanPreviewRead:
    if meeting.status != ScheduledMeetingStatus.CREATED:
        raise ScheduledMeetingPlanPreviewError(
            "Проверка конфликтов доступна только для серии со статусом created",
            status_code=409,
        )
    if meeting.outlook_series_id:
        raise ScheduledMeetingPlanPreviewError(
            "Серия уже распланирована в Outlook",
            status_code=409,
        )

    try:
        attendee_emails = _normalize_emails(await resolve_attendee_emails(db, meeting))
    except ScheduledMeetingOutlookError as exc:
        raise ScheduledMeetingPlanPreviewError(str(exc), status_code=exc.status_code) from exc

    occurrences = build_occurrences_from_rule(
        meeting,
        range_start=meeting.series_start_date,
        range_end=meeting.series_end_date,
    )
    if not occurrences:
        raise ScheduledMeetingPlanPreviewError("У серии нет дат для планирования")

    config = load_config()
    timezone_name = config.timezone or settings.OUTLOOK_TIMEZONE
    window_start, window_end = _freebusy_window(occurrences, timezone_name=timezone_name)

    if attendee_emails:
        busy_by_attendee, events_by_attendee = await asyncio.wait_for(
            asyncio.to_thread(
                _fetch_freebusy_chunked,
                config,
                attendee_emails,
                window_start,
                window_end,
            ),
            timeout=SLOT_PREVIEW_TIMEOUT_SECONDS,
        )
        busy_by_attendee = {
            email.strip().lower(): intervals
            for email, intervals in busy_by_attendee.items()
        }
        events_by_attendee = {
            email.strip().lower(): events
            for email, events in events_by_attendee.items()
        }
    else:
        busy_by_attendee = {}
        events_by_attendee = {}

    preview_items: list[ScheduledMeetingPlanOccurrencePreview] = []
    for occurrence in occurrences:
        preview_items.append(
            evaluate_occurrence_preview(
                occurrence=occurrence,
                conflict_policy=conflict_policy,
                attendees=attendee_emails,
                busy_by_attendee=busy_by_attendee,
                events_by_attendee=events_by_attendee,
                config=config,
                anchor_weekday=getattr(meeting, "weekday", None),
            )
        )

    summary = {
        "total": len(preview_items),
        "ok": sum(1 for item in preview_items if item.status == "ok"),
        "conflict": sum(1 for item in preview_items if item.status == "conflict"),
        "shifted": sum(1 for item in preview_items if item.status == "shifted"),
        "skip": sum(1 for item in preview_items if item.status == "skip"),
        "unresolved": sum(1 for item in preview_items if item.status == "unresolved"),
    }
    return ScheduledMeetingPlanPreviewRead(
        meeting_id=meeting.id,
        conflict_policy=conflict_policy,
        occurrences=preview_items,
        summary=summary,
    )
