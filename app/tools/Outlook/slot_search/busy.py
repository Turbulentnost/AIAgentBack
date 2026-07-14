from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

from app.tools.Outlook.cancel_meeting import to_ews, to_local
from app.tools.Outlook.outlook_config import OutlookConfig
from app.tools.Outlook.read_calendars import connect_as_owner, read_calendar_items_in_range
from app.tools.Outlook.send_meeting_invite import connect_account

from .constants import (
    BUSY_STATUSES,
    FREE_BUSY_MERGED_INTERVAL_MINUTES,
    MERGED_FREE_CHARS,
)
from .rules import intervals_overlap
from .timing import logger, timed_step


def event_interval(item: Any, config: OutlookConfig) -> tuple[datetime, datetime] | None:
    if item.is_cancelled:
        return None
    status = str(item.legacy_free_busy_status or "")
    if status and status not in BUSY_STATUSES:
        return None
    start = to_local(item.start, config)
    end = to_local(item.end, config)
    if end <= start:
        return None
    return start, end

def freebusy_event_interval(event: Any, config: OutlookConfig) -> tuple[datetime, datetime] | None:
    status = str(getattr(event, "busy_type", "") or "")
    if status == "Free":
        return None
    if not status or status not in BUSY_STATUSES:
        return None
    start = to_local(event.start, config)
    end = to_local(event.end, config)
    if end <= start:
        return None
    return start, end

def parse_freebusy_events(events: list[Any], config: OutlookConfig) -> list[tuple[datetime, datetime]]:
    intervals: list[tuple[datetime, datetime]] = []
    for event in events:
        interval = freebusy_event_interval(event, config)
        if interval:
            intervals.append(interval)
    return intervals

def busy_intervals_from_merged_string(
    merged: str,
    range_start: datetime,
    range_end: datetime,
    config: OutlookConfig,
    *,
    interval_minutes: int = FREE_BUSY_MERGED_INTERVAL_MINUTES,
) -> list[tuple[datetime, datetime]]:
    """Парсит merged-строку FreeBusyView (0=свободен, 1+=занят/предварительно/OOF)."""
    if not merged:
        return []

    local_start = to_local(range_start, config).replace(second=0, microsecond=0)
    local_end = to_local(range_end, config).replace(second=0, microsecond=0)
    step = timedelta(minutes=interval_minutes)
    intervals: list[tuple[datetime, datetime]] = []
    busy_start: datetime | None = None
    slot_start = local_start

    for char in merged:
        slot_end = slot_start + step
        if slot_start >= local_end:
            break
        if char not in MERGED_FREE_CHARS:
            if busy_start is None:
                busy_start = slot_start
        elif busy_start is not None:
            intervals.append((busy_start, slot_start))
            busy_start = None
        slot_start = slot_end

    if busy_start is not None:
        intervals.append((busy_start, min(slot_start, local_end)))
    return intervals

def freebusy_busy_intervals(
    view: Any,
    *,
    attendee: str,
    range_start: datetime,
    range_end: datetime,
    config: OutlookConfig,
) -> list[tuple[datetime, datetime]]:
    # merged — каноничная сетка Free/Busy; calendar_events часто даёт лишнюю «занятость».
    merged = getattr(view, "merged", None)
    if isinstance(merged, str) and merged:
        return busy_intervals_from_merged_string(merged, range_start, range_end, config)

    events = getattr(view, "calendar_events", None)
    if events is not None and list(events or []):
        return parse_freebusy_events(list(events), config)

    message = getattr(view, "message", None)
    if message:
        raise RuntimeError(f"Exchange не вернул занятость для {attendee}: {message}")
    raise RuntimeError(f"Exchange не вернул занятость для {attendee}: пустой ответ FreeBusyView")

def freebusy_events_busy_intervals(
    view: Any,
    *,
    attendee: str,
    range_start: datetime,
    range_end: datetime,
    config: OutlookConfig,
) -> list[tuple[datetime, datetime]]:
    """Для проверки слота: calendar_events из GetUserAvailability, затем merged."""
    events = getattr(view, "calendar_events", None)
    if events is not None and list(events or []):
        intervals = parse_freebusy_events(list(events), config)
        if intervals:
            return intervals

    merged = getattr(view, "merged", None)
    if isinstance(merged, str) and merged:
        return busy_intervals_from_merged_string(merged, range_start, range_end, config)

    message = getattr(view, "message", None)
    if message:
        raise RuntimeError(f"Exchange не вернул занятость для {attendee}: {message}")
    return []

def calendar_events_from_freebusy_view(view: Any, attendee: str) -> list[Any]:
    events = getattr(view, "calendar_events", None)
    if events is not None:
        return list(events or [])
    merged = getattr(view, "merged", None)
    if isinstance(merged, str) and merged and all(char in MERGED_FREE_CHARS for char in merged):
        return []
    message = getattr(view, "message", None) or getattr(view, "view_type", None) or "FreeBusyView"
    raise RuntimeError(f"Exchange не вернул занятость для {attendee}: {message}")

def fetch_free_busy_views(
    config: OutlookConfig,
    attendees: list[str],
    range_start: datetime,
    range_end: datetime,
) -> dict[str, Any]:
    """GetUserAvailability — один запрос, ответ по каждому участнику."""
    attendee_list = [email.strip() for email in attendees]
    with timed_step("ews.connect.service"):
        service_account = connect_account(config)

    mailbox_data = [(email, "Required", False) for email in attendee_list]
    with timed_step("ews.freebusy.get", attendees=len(attendee_list)):
        views = list(
            service_account.protocol.get_free_busy_info(
                mailbox_data,
                start=to_ews(range_start, config),
                end=to_ews(range_end, config),
                requested_view="DetailedMerged",
            )
        )

    if len(views) != len(attendee_list):
        raise RuntimeError(
            f"Free/busy вернул {len(views)} ответов для {len(attendee_list)} участников"
        )
    return dict(zip(attendee_list, views))

def fetch_busy_intervals_freebusy(
    config: OutlookConfig,
    attendees: list[str],
    range_start: datetime,
    range_end: datetime,
) -> dict[str, list[tuple[datetime, datetime]]]:
    """GetUserAvailability — один запрос на всех участников (быстро)."""
    views_by_email = fetch_free_busy_views(config, attendees, range_start, range_end)

    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]] = {}
    for email, view in views_by_email.items():
        with timed_step("parse.freebusy_intervals", attendee=email):
            try:
                intervals = freebusy_busy_intervals(
                    view,
                    attendee=email,
                    range_start=range_start,
                    range_end=range_end,
                    config=config,
                )
            except RuntimeError as exc:
                logger.warning(
                    "Free/busy недоступен для %s, пробуем calendar.view: %s",
                    email,
                    exc,
                )
                intervals = fetch_busy_intervals_calendar(
                    config,
                    email,
                    range_start,
                    range_end,
                    max_items=500,
                )
        busy_by_attendee[email] = intervals
        merged = getattr(view, "merged", None)
        merged_len = len(merged) if isinstance(merged, str) else 0
        busy_chars = (
            sum(1 for char in merged if char not in MERGED_FREE_CHARS)
            if isinstance(merged, str)
            else 0
        )
        logger.info(
            "  %s: занятых интервалов=%d, merged_len=%d, busy_chars=%d",
            email,
            len(intervals),
            merged_len,
            busy_chars,
        )
    return busy_by_attendee

def fetch_busy_intervals_freebusy_events(
    config: OutlookConfig,
    attendees: list[str],
    range_start: datetime,
    range_end: datetime,
) -> dict[str, list[tuple[datetime, datetime]]]:
    """Проверка занятости через calendar_events из GetUserAvailability (без Delegate на папку)."""
    views_by_email = fetch_free_busy_views(config, attendees, range_start, range_end)
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]] = {}
    for email, view in views_by_email.items():
        with timed_step("parse.freebusy_events", attendee=email):
            intervals = freebusy_events_busy_intervals(
                view,
                attendee=email,
                range_start=range_start,
                range_end=range_end,
                config=config,
            )
        busy_by_attendee[email] = intervals
        logger.info("  %s: занятых интервалов (events)=%d", email, len(intervals))
    return busy_by_attendee

def fetch_busy_intervals_calendar(
    config: OutlookConfig,
    email: str,
    range_start: datetime,
    range_end: datetime,
    *,
    max_items: int,
) -> list[tuple[datetime, datetime]]:
    email = email.strip()
    try:
        with timed_step("ews.connect", attendee=email):
            account = connect_as_owner(config, email)
        with timed_step("ews.calendar.view", attendee=email, max_items=max_items):
            items = list(
                account.calendar.view(
                    start=to_ews(range_start, config),
                    end=to_ews(range_end, config),
                    max_items=max_items,
                )
            )
    except Exception as error:
        raise RuntimeError(
            f"Не удалось прочитать календарь {email}: {error}"
        ) from error

    with timed_step("parse.busy_intervals", attendee=email):
        intervals: list[tuple[datetime, datetime]] = []
        for item in items:
            interval = event_interval(item, config)
            if interval:
                intervals.append(interval)

    logger.info(
        "  %s: событий=%d, занятых интервалов=%d",
        email,
        len(items),
        len(intervals),
    )
    return intervals

def fetch_all_busy_intervals(
    config: OutlookConfig,
    attendees: list[str],
    range_start: datetime,
    range_end: datetime,
    *,
    source: AvailabilitySource,
    max_items: int,
    workers: int,
) -> dict[str, list[tuple[datetime, datetime]]]:
    if source == "freebusy":
        return fetch_busy_intervals_freebusy(
            config,
            attendees,
            range_start,
            range_end,
        )

    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]] = {}
    worker_count = max(1, min(workers, len(attendees)))

    if worker_count == 1 or len(attendees) == 1:
        for email in attendees:
            busy_by_attendee[email] = fetch_busy_intervals_calendar(
                config,
                email,
                range_start,
                range_end,
                max_items=max_items,
            )
        return busy_by_attendee

    logger.info("Параллельная загрузка calendar.view (%d потоков) ...", worker_count)
    with timed_step("fetch.calendars.parallel", workers=worker_count, attendees=len(attendees)):
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {
                pool.submit(
                    fetch_busy_intervals_calendar,
                    config,
                    email,
                    range_start,
                    range_end,
                    max_items=max_items,
                ): email
                for email in attendees
            }
            for future in as_completed(futures):
                email = futures[future]
                busy_by_attendee[email] = future.result()
    return busy_by_attendee

def fetch_freebusy_calendar_events(
    config: OutlookConfig,
    attendees: list[str],
    range_start: datetime,
    range_end: datetime,
) -> dict[str, list[Any]]:
    views_by_email = fetch_free_busy_views(config, attendees, range_start, range_end)
    return {
        email: list(getattr(view, "calendar_events", None) or [])
        for email, view in views_by_email.items()
    }


def busy_intervals_and_events_from_freebusy(
    config: OutlookConfig,
    attendees: list[str],
    range_start: datetime,
    range_end: datetime,
    *,
    max_items: int = 500,
) -> tuple[dict[str, list[tuple[datetime, datetime]]], dict[str, list[Any]]]:
    """Один GetUserAvailability: занятость + calendar_events по участникам."""
    attendee_list = [email.strip() for email in attendees if email.strip()]
    if not attendee_list:
        return {}, {}

    views_by_email = fetch_free_busy_views(config, attendee_list, range_start, range_end)
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]] = {}
    events_by_attendee: dict[str, list[Any]] = {}

    for email, view in views_by_email.items():
        with timed_step("parse.freebusy_intervals", attendee=email):
            try:
                intervals = freebusy_busy_intervals(
                    view,
                    attendee=email,
                    range_start=range_start,
                    range_end=range_end,
                    config=config,
                )
            except RuntimeError as exc:
                logger.warning(
                    "Free/busy недоступен для %s, пробуем calendar.view: %s",
                    email,
                    exc,
                )
                intervals = fetch_busy_intervals_calendar(
                    config,
                    email,
                    range_start,
                    range_end,
                    max_items=max_items,
                )
        busy_by_attendee[email] = intervals
        events_by_attendee[email] = list(getattr(view, "calendar_events", None) or [])
        merged = getattr(view, "merged", None)
        merged_len = len(merged) if isinstance(merged, str) else 0
        busy_chars = (
            sum(1 for char in merged if char not in MERGED_FREE_CHARS)
            if isinstance(merged, str)
            else 0
        )
        logger.info(
            "  %s: занятых интервалов=%d, merged_len=%d, busy_chars=%d",
            email,
            len(intervals),
            merged_len,
            busy_chars,
        )

    return busy_by_attendee, events_by_attendee

def coalesce_intervals(
    intervals: list[tuple[datetime, datetime]],
    config: OutlookConfig,
    *,
    clip_start: datetime | None = None,
    clip_end: datetime | None = None,
) -> list[tuple[datetime, datetime]]:
    """Склеивает пересекающиеся интервалы занятости."""
    if not intervals:
        return []
    clip_start_local = to_local(clip_start, config) if clip_start else None
    clip_end_local = to_local(clip_end, config) if clip_end else None
    normalized: list[tuple[datetime, datetime]] = []
    for start, end in intervals:
        local_start = to_local(start, config)
        local_end = to_local(end, config)
        if local_end <= local_start:
            continue
        if clip_start_local is not None:
            local_start = max(local_start, clip_start_local)
        if clip_end_local is not None:
            local_end = min(local_end, clip_end_local)
        if local_end <= local_start:
            continue
        normalized.append((local_start, local_end))
    if not normalized:
        return []
    normalized.sort(key=lambda item: item[0])
    merged: list[tuple[datetime, datetime]] = [normalized[0]]
    for start, end in normalized[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged

def merge_busy_intervals(
    *sources: dict[str, list[tuple[datetime, datetime]]],
) -> dict[str, list[tuple[datetime, datetime]]]:
    merged: dict[str, list[tuple[datetime, datetime]]] = {}
    for source in sources:
        for email, intervals in source.items():
            bucket = merged.setdefault(email, [])
            bucket.extend(intervals)
    return merged

