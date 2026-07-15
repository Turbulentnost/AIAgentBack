from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.models.scheduled_meeting import ScheduledMeeting
from app.services.scheduled_meeting_recurrence import (
    RecurrenceInput,
    iter_occurrence_dates,
    occurrence_slot_bounds,
)
from app.tools.Outlook.meeting_series import meeting_kind, series_master_id
from app.tools.Outlook.outlook_config import OutlookConfig
from app.tools.Outlook.read_calendars import read_calendar_items_in_range
from app.tools.Outlook.send_meeting_invite import load_config, primary_smtp_address

logger = logging.getLogger(__name__)

OccurrenceSource = Literal["outlook", "rule"]


@dataclass(frozen=True)
class SeriesOccurrence:
    occurrence_date: date
    slot_start: datetime
    slot_end: datetime
    outlook_item_id: str | None
    outlook_changekey: str | None
    subject: str
    is_cancelled: bool
    source: OccurrenceSource


def recurrence_input_from_meeting(meeting: ScheduledMeeting) -> RecurrenceInput:
    return RecurrenceInput(
        frequency=meeting.frequency,
        interval=meeting.interval,
        time_local=meeting.time_local,
        duration_minutes=meeting.duration_minutes,
        series_start_date=meeting.series_start_date,
        series_end_date=meeting.series_end_date,
        monthly_mode=meeting.monthly_mode,
        day_of_month=meeting.day_of_month,
        weekday=meeting.weekday,
        weekday_position=meeting.weekday_position,
    )


def _ews_datetime_to_aware(value: Any, *, timezone_name: str) -> datetime:
    if value is None:
        raise ValueError("datetime value is required")
    if hasattr(value, "astimezone"):
        converted = value.astimezone(ZoneInfo(timezone_name))
        return converted
    normalized = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed.astimezone(ZoneInfo(timezone_name))


def _calendar_item_to_occurrence(
    item: Any,
    *,
    timezone_name: str,
) -> SeriesOccurrence | None:
    if bool(getattr(item, "is_cancelled", False)):
        return None
    if meeting_kind(item) != "series_occurrence":
        return None
    slot_start = _ews_datetime_to_aware(getattr(item, "start", None), timezone_name=timezone_name)
    slot_end = _ews_datetime_to_aware(getattr(item, "end", None), timezone_name=timezone_name)
    return SeriesOccurrence(
        occurrence_date=slot_start.date(),
        slot_start=slot_start,
        slot_end=slot_end,
        outlook_item_id=str(getattr(item, "id", None) or "") or None,
        outlook_changekey=str(getattr(item, "changekey", None) or "") or None,
        subject=str(getattr(item, "subject", None) or "").strip(),
        is_cancelled=False,
        source="outlook",
    )


def fetch_series_occurrences_from_outlook(
    outlook_series_id: str,
    *,
    range_start: datetime,
    range_end: datetime,
    config: OutlookConfig | None = None,
    owner_smtp: str | None = None,
) -> list[SeriesOccurrence]:
    resolved_config = config or load_config()
    mailbox = (owner_smtp or resolved_config.mailbox or primary_smtp_address(resolved_config)).strip()
    if not mailbox:
        raise RuntimeError("Не задан mailbox для чтения календаря серии")
    items = read_calendar_items_in_range(
        resolved_config,
        mailbox,
        range_start=range_start,
        range_end=range_end,
        max_items=500,
    )
    occurrences: list[SeriesOccurrence] = []
    timezone_name = settings.OUTLOOK_TIMEZONE
    for item in items:
        if series_master_id(item) != outlook_series_id:
            continue
        occurrence = _calendar_item_to_occurrence(item, timezone_name=timezone_name)
        if occurrence is not None:
            occurrences.append(occurrence)
    occurrences.sort(key=lambda item: item.slot_start)
    return occurrences


def build_occurrences_from_rule(
    meeting: ScheduledMeeting,
    *,
    range_start: date,
    range_end: date,
) -> list[SeriesOccurrence]:
    recurrence = recurrence_input_from_meeting(meeting)
    timezone_name = settings.OUTLOOK_TIMEZONE
    occurrences: list[SeriesOccurrence] = []
    for occurrence_date in iter_occurrence_dates(
        recurrence,
        range_start=range_start,
        range_end=range_end,
    ):
        slot_start, slot_end = occurrence_slot_bounds(
            occurrence_date,
            time_local=recurrence.time_local,
            duration_minutes=recurrence.duration_minutes,
            timezone_name=timezone_name,
        )
        occurrences.append(
            SeriesOccurrence(
                occurrence_date=occurrence_date,
                slot_start=slot_start,
                slot_end=slot_end,
                outlook_item_id=None,
                outlook_changekey=None,
                subject=meeting.title.strip(),
                is_cancelled=False,
                source="rule",
            )
        )
    return occurrences


def resolve_series_occurrences(
    meeting: ScheduledMeeting,
    *,
    range_start: date,
    range_end: date,
    now: datetime | None = None,
) -> tuple[list[SeriesOccurrence], OccurrenceSource | Literal["none"]]:
    timezone_name = settings.OUTLOOK_TIMEZONE
    tz = ZoneInfo(timezone_name)
    anchor = now.astimezone(tz) if now is not None else datetime.now(tz)
    start_dt = datetime.combine(range_start, datetime.min.time(), tzinfo=tz)
    end_dt = datetime.combine(range_end + timedelta(days=1), datetime.min.time(), tzinfo=tz)

    if meeting.outlook_series_id:
        try:
            occurrences = fetch_series_occurrences_from_outlook(
                meeting.outlook_series_id,
                range_start=start_dt,
                range_end=end_dt,
            )
            if occurrences:
                return occurrences, "outlook"
        except Exception as exc:
            logger.warning(
                "scheduled_series_outlook_occurrences_failed series_id=%s error=%s",
                meeting.id,
                exc,
            )

    rule_occurrences = build_occurrences_from_rule(
        meeting,
        range_start=range_start,
        range_end=range_end,
    )
    if rule_occurrences:
        return rule_occurrences, "rule"
    return [], "none"


def find_next_occurrence(
    occurrences: list[SeriesOccurrence],
    *,
    now: datetime,
) -> SeriesOccurrence | None:
    for occurrence in sorted(occurrences, key=lambda item: item.slot_start):
        if occurrence.slot_end >= now:
            return occurrence
    return None


def find_next_after(
    occurrences: list[SeriesOccurrence],
    *,
    after_date: date,
) -> SeriesOccurrence | None:
    for occurrence in sorted(occurrences, key=lambda item: item.slot_start):
        if occurrence.occurrence_date > after_date:
            return occurrence
    return None


def find_occurrence_on_date(
    occurrences: list[SeriesOccurrence],
    *,
    occurrence_date: date,
) -> SeriesOccurrence | None:
    for occurrence in occurrences:
        if occurrence.occurrence_date == occurrence_date:
            return occurrence
    return None
