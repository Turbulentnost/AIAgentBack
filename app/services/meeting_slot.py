from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.services.meeting_constants import SLOT_PREVIEW_MAX_DAYS
from app.services.meeting_memo_document import parse_odata_datetime, resolve_meeting_schedule

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
    if day_start.tzinfo is None:
        day_start = day_start.replace(tzinfo=display_timezone())
    else:
        day_start = day_start.astimezone(display_timezone())
    return format_datetime_for_search(day_start)


def format_attendee_nearest_slot_search_start(
    meeting_start: str | None,
    queue: dict[str, Any] | None = None,
) -> str:
    """Точка отсчёта персонального «ближайшего слота»: с сегодня, не только с даты СЗ."""
    from app.tools.Outlook.outlook_config import build_outlook_config
    from app.tools.Outlook.slot_search.rules import not_before_now

    config = build_outlook_config()
    today_start = format_datetime_for_search(not_before_now(config))
    meeting_day_start = format_search_start_from_meeting_date(meeting_start, queue)
    if not meeting_day_start:
        return today_start

    today_dt = parse_slot_datetime(today_start)
    meeting_dt = parse_slot_datetime(meeting_day_start)
    if today_dt is None or meeting_dt is None:
        return today_start
    return today_start if today_dt <= meeting_dt else meeting_day_start


def format_search_start_after_registry_slot(
    slot_start: datetime | None,
    slot_end: datetime | None,
) -> str | None:
    """Точка поиска для переноса: сразу после окончания текущего слота в реестре."""
    anchor = slot_end or slot_start
    if anchor is None:
        return None
    return format_datetime_for_search(anchor)


@dataclass(frozen=True)
class RegistryEarlierSlotWindow:
    lower_bound: datetime
    upper_bound: datetime
    duration_minutes: int
    search_from_label: str
    search_until_label: str
    current_slot_label: str


def _day_start_at_eight(day: datetime) -> datetime:
    tz = display_timezone()
    if day.tzinfo is not None:
        day_local = day.astimezone(tz)
    else:
        day_local = day.replace(tzinfo=tz)
    return day_local.replace(hour=8, minute=0, second=0, microsecond=0)


def _resolve_desired_day_from_memo(memo_detail: dict[str, Any] | None) -> datetime | None:
    application = (memo_detail or {}).get("application") or {}
    queue = (memo_detail or {}).get("queue") or {}

    desired_raw = queue.get("desired_meeting_date")
    if isinstance(desired_raw, str) and desired_raw.strip():
        parsed = parse_slot_datetime(desired_raw) or parse_odata_datetime(desired_raw)
        if parsed is not None:
            return parsed

    meeting_start = application.get("meeting_start")
    if isinstance(meeting_start, str) and meeting_start.strip():
        parsed = parse_slot_datetime(meeting_start)
        if parsed is not None:
            return parsed

    scheduled, _end = resolve_meeting_schedule(queue)
    return scheduled


def resolve_registry_earlier_slot_window(
    entry: Any,
    memo_detail: dict[str, Any] | None,
) -> RegistryEarlierSlotWindow | None:
    """Окно поиска более раннего слота: [желаемая дата СЗ 08:00, текущий slot_start)."""
    slot_start = getattr(entry, "slot_start", None)
    slot_end = getattr(entry, "slot_end", None)
    if slot_start is None:
        return None

    upper_bound = slot_start
    if upper_bound.tzinfo is None:
        upper_bound = upper_bound.replace(tzinfo=timezone.utc)

    desired_day = _resolve_desired_day_from_memo(memo_detail)
    if desired_day is None:
        return None

    lower_bound = _day_start_at_eight(desired_day)
    if lower_bound.tzinfo is None:
        lower_bound = lower_bound.replace(tzinfo=upper_bound.tzinfo)
    elif upper_bound.tzinfo is not None:
        lower_bound = lower_bound.astimezone(upper_bound.tzinfo)

    if lower_bound >= upper_bound:
        return None

    if slot_end is not None and slot_start is not None:
        duration_minutes = max(int((slot_end - slot_start).total_seconds() // 60), 1)
    else:
        duration_minutes = 60

    current_end = slot_end.isoformat() if slot_end is not None else upper_bound.isoformat()
    return RegistryEarlierSlotWindow(
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        duration_minutes=duration_minutes,
        search_from_label=format_datetime_for_search(lower_bound),
        search_until_label=format_datetime_for_search(upper_bound),
        current_slot_label=format_slot_label(slot_start.isoformat(), current_end),
    )


@dataclass(frozen=True)
class RegistryCommonSlotWindow:
    lower_bound: datetime
    upper_bound: datetime
    duration_minutes: int
    search_from_label: str
    search_until_label: str
    current_slot_label: str


def resolve_registry_common_slot_window(
    entry: Any,
    *,
    max_days: int = SLOT_PREVIEW_MAX_DAYS,
) -> RegistryCommonSlotWindow | None:
    """Окно поиска общего слота после добавления участника: [текущий slot_start, +max_days)."""
    slot_start = getattr(entry, "slot_start", None)
    slot_end = getattr(entry, "slot_end", None)
    if slot_start is None:
        return None

    lower_bound = slot_start
    if lower_bound.tzinfo is None:
        lower_bound = lower_bound.replace(tzinfo=timezone.utc)

    horizon_days = max(1, min(max_days, SLOT_PREVIEW_MAX_DAYS))
    upper_bound = lower_bound + timedelta(days=horizon_days)
    if slot_end is not None and slot_start is not None:
        duration_minutes = max(int((slot_end - slot_start).total_seconds() // 60), 1)
    else:
        duration_minutes = 60

    current_end = slot_end.isoformat() if slot_end is not None else lower_bound.isoformat()
    return RegistryCommonSlotWindow(
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        duration_minutes=duration_minutes,
        search_from_label=format_datetime_for_search(lower_bound),
        search_until_label=format_datetime_for_search(upper_bound),
        current_slot_label=format_slot_label(slot_start.isoformat(), current_end),
    )
