from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.services.meeting_memo_document import resolve_meeting_schedule

DISPLAY_TIMEZONE = (settings.OUTLOOK_TIMEZONE or "Europe/Moscow").strip() or "Europe/Moscow"


def display_timezone() -> ZoneInfo:
    return ZoneInfo(DISPLAY_TIMEZONE)


def to_display_local(dt: datetime) -> datetime:
    """Переводит момент времени в часовой пояс отображения (как в Outlook/UI)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(display_timezone())


def format_datetime_for_search(dt: datetime) -> str:
    return to_display_local(dt).strftime("%Y-%m-%d %H:%M")


def parse_slot_datetime(value: str) -> datetime | None:
    normalized = str(value).strip().replace(" ", "T").replace("Z", "+00:00")
    if not normalized:
        return None
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def slot_duration_minutes(slot_start: str, slot_end: str, *, default: int = 60) -> int:
    start_dt = parse_slot_datetime(slot_start)
    end_dt = parse_slot_datetime(slot_end)
    if start_dt is None or end_dt is None:
        return default
    minutes = int((end_dt - start_dt).total_seconds() // 60)
    return minutes if minutes > 0 else default


def format_slot_label(start: str, end: str) -> str:
    start_dt = parse_slot_datetime(start)
    end_dt = parse_slot_datetime(end)
    if start_dt is None or end_dt is None:
        return start
    start_local = to_display_local(start_dt)
    end_local = to_display_local(end_dt)
    if start_local.date() == end_local.date():
        start_label = start_local.strftime("%d.%m.%Y, %H:%M")
        end_label = end_local.strftime("%H:%M")
        return f"{start_label}–{end_label}"
    return start_local.strftime("%d.%m.%Y, %H:%M")


def format_event_time_display(
    event_start: str | None,
    event_end: str | None,
) -> tuple[str | None, str | None]:
    """Форматирует интервал конфликта для UI: дата + время, без ISO."""
    if not event_start or not event_end:
        return event_start, event_end
    start_dt = parse_slot_datetime(str(event_start))
    end_dt = parse_slot_datetime(str(event_end))
    if start_dt is None or end_dt is None:
        return event_start, event_end
    start_local = to_display_local(start_dt)
    end_local = to_display_local(end_dt)
    if start_local.date() == end_local.date():
        return (
            start_local.strftime("%d.%m.%Y, %H:%M"),
            end_local.strftime("%H:%M"),
        )
    return (
        start_local.strftime("%d.%m.%Y, %H:%M"),
        end_local.strftime("%d.%m.%Y, %H:%M"),
    )


def format_planned_start_for_search(
    meeting_start: str | None,
    queue: dict[str, Any] | None = None,
) -> str | None:
    """Желаемое начало для find_meeting_slot: дата + время, не раньше указанного в СЗ."""
    if isinstance(meeting_start, str) and meeting_start.strip():
        parsed = parse_slot_datetime(meeting_start)
        if parsed is not None:
            return format_datetime_for_search(parsed)

    header = dict(queue or {})
    start, _end = resolve_meeting_schedule(header)
    if start is not None:
        return start.strftime("%Y-%m-%d %H:%M")
    return None


def format_search_start_from_meeting_date(
    meeting_start: str | None,
    queue: dict[str, Any] | None = None,
) -> str | None:
    """08:00 даты совещания из СЗ — точка отсчёта персонального поиска слота."""
    day: datetime | None = None
    if isinstance(meeting_start, str) and meeting_start.strip():
        day = parse_slot_datetime(meeting_start)
    if day is None:
        header = dict(queue or {})
        scheduled, _end = resolve_meeting_schedule(header)
        day = scheduled
    if day is None:
        return None
    day_start = day.replace(hour=8, minute=0, second=0, microsecond=0)
    if day_start.tzinfo is not None:
        day_start = day_start.replace(tzinfo=None)
    return day_start.strftime("%Y-%m-%d %H:%M")


def format_search_start_after_registry_slot(
    slot_start: datetime | None,
    slot_end: datetime | None,
) -> str | None:
    """Точка поиска для переноса: сразу после окончания текущего слота в реестре."""
    anchor = slot_end or slot_start
    if anchor is None:
        return None
    return format_datetime_for_search(anchor)
