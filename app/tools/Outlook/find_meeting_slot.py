"""
Поиск ближайшего свободного слота для совещания у нескольких участников (EWS).

Правила:
  - только рабочие дни (пн–пт);
  - время 08:00–17:00 (совещание должно полностью уложиться);
  - запрещены пересечения с обеденным перерывом 12:00–13:00.

Пример:
  python -m app.tools.Outlook.find_meeting_slot \\
    --attendee sktb_razvitie9@turbo-don.ru \\
    --preferred "2026-06-10 14:00" \\
    --duration 60

Логи производительности пишутся в stderr (шаг, мс, %). Отключить: --quiet
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time as time_module
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta
from datetime import time as dt_time
from typing import Any, Iterator, Literal
from zoneinfo import ZoneInfo

from app.tools.Outlook.cancel_meeting import to_ews, to_local
from app.tools.Outlook.meeting_rooms import (
    DEFAULT_ROOMS_FILE,
    check_rooms_status,
    format_rooms_status,
    load_rooms,
)
from app.tools.Outlook.outlook_config import OutlookConfig
from app.tools.Outlook.ews_logging import configure_exchangelib_logging
from app.tools.Outlook.read_calendars import connect_as_owner, read_calendar_items_in_range
from app.tools.Outlook.send_meeting_invite import connect_account, load_config, parse_start

AvailabilitySource = Literal["freebusy", "calendar"]

WORK_START = dt_time(8, 0)
WORK_END = dt_time(17, 0)
FORBIDDEN_BLOCKS = (
    (dt_time(12, 0), dt_time(13, 0)),
)
BUSY_STATUSES = frozenset({"Busy", "Tentative", "OOF", "WorkingElsewhere"})
FREE_BUSY_MERGED_INTERVAL_MINUTES = 30
MERGED_FREE_CHARS = frozenset({"0", ""})

logger = logging.getLogger("find_meeting_slot")
_timing_report: list[dict[str, Any]] = []
_run_started_at: float | None = None


def setup_logging(*, quiet: bool) -> None:
    # Не использовать logging.disable(): он глушит весь процесс (включая uvicorn/structlog).
    logging.disable(logging.NOTSET)
    configure_exchangelib_logging(verbose=not quiet)
    logger.propagate = False
    if quiet:
        logger.setLevel(logging.CRITICAL + 1)
        return
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            RelativeMsFormatter("%(levelname)s [+%(relative)7.0f ms] %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class RelativeMsFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        global _run_started_at
        if _run_started_at is None:
            _run_started_at = time_module.perf_counter()
        record.relative = (time_module.perf_counter() - _run_started_at) * 1000  # type: ignore[attr-defined]
        return super().format(record)


def reset_timing_report() -> None:
    global _run_started_at
    _timing_report.clear()
    _run_started_at = time_module.perf_counter()


def record_timing(step: str, elapsed_ms: float, **details: Any) -> None:
    entry: dict[str, Any] = {"step": step, "elapsed_ms": round(elapsed_ms, 1)}
    entry.update(details)
    _timing_report.append(entry)


@contextmanager
def timed_step(step: str, **details: Any) -> Iterator[None]:
    started = time_module.perf_counter()
    detail_text = ", ".join(f"{key}={value}" for key, value in details.items())
    logger.info("→ %s%s", step, f" ({detail_text})" if detail_text else "")
    try:
        yield
    finally:
        elapsed_ms = (time_module.perf_counter() - started) * 1000
        record_timing(step, elapsed_ms, **details)
        logger.info("✓ %s: %.0f ms", step, elapsed_ms)


def log_timing_summary() -> None:
    if not _timing_report:
        return
    total_ms = sum(entry["elapsed_ms"] for entry in _timing_report)
    logger.info("--- сводка по времени (%.0f ms всего) ---", total_ms)
    for entry in _timing_report:
        share = (entry["elapsed_ms"] / total_ms * 100) if total_ms else 0.0
        detail_text = ", ".join(
            f"{key}={value}"
            for key, value in entry.items()
            if key not in {"step", "elapsed_ms"}
        )
        suffix = f" ({detail_text})" if detail_text else ""
        logger.info(
            "  %.0f ms (%5.1f%%) %s%s",
            entry["elapsed_ms"],
            share,
            entry["step"],
            suffix,
        )


def combine(day: datetime, clock: dt_time, config: OutlookConfig) -> datetime:
    day = to_local(day, config)
    return day.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)


def is_workday(dt: datetime, config: OutlookConfig) -> bool:
    return to_local(dt, config).weekday() < 5


def next_workday_start(dt: datetime, config: OutlookConfig) -> datetime:
    dt = to_local(dt, config)
    candidate = combine(dt, WORK_START, config)
    if candidate <= dt:
        candidate += timedelta(days=1)
    while not is_workday(candidate, config):
        candidate += timedelta(days=1)
    return candidate


def align_preferred(preferred: datetime, config: OutlookConfig) -> datetime:
    """Первая точка перебора с учётом рабочего дня (не раньше WORK_START)."""
    current = to_local(preferred, config).replace(second=0, microsecond=0)
    if not is_workday(current, config):
        while not is_workday(current, config):
            current += timedelta(days=1)
        return combine(current, WORK_START, config)
    if current.time() < WORK_START:
        return combine(current, WORK_START, config)
    latest_start = combine(current, WORK_END, config) - timedelta(minutes=1)
    if current > latest_start:
        return next_workday_start(current, config)
    return current


def not_before_now(config: OutlookConfig) -> datetime:
    """Нижняя граница поиска — текущий момент (не дата из СЗ в прошлом)."""
    now = datetime.now(ZoneInfo(config.timezone)).replace(second=0, microsecond=0)
    return align_preferred(now, config)


def intervals_overlap(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and a_end > b_start


def slot_respects_rules(start: datetime, duration: timedelta, config: OutlookConfig) -> bool:
    start = to_local(start, config)
    end = start + duration
    if not is_workday(start, config):
        return False
    if start.date() != end.date():
        return False
    if start.time() < WORK_START or end.time() > WORK_END:
        return False
    for block_start, block_end in (
        (combine(start, block[0], config), combine(start, block[1], config))
        for block in FORBIDDEN_BLOCKS
    ):
        if intervals_overlap(start, end, block_start, block_end):
            return False
    return True


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


def is_free_for_attendee(
    start: datetime,
    duration: timedelta,
    busy_intervals: list[tuple[datetime, datetime]],
    config: OutlookConfig,
) -> bool:
    local_start = to_local(start, config)
    local_end = local_start + duration
    for busy_start, busy_end in busy_intervals:
        if intervals_overlap(
            local_start,
            local_end,
            to_local(busy_start, config),
            to_local(busy_end, config),
        ):
            return False
    return True


def is_free_for_all(
    start: datetime,
    duration: timedelta,
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    config: OutlookConfig,
) -> bool:
    for intervals in busy_by_attendee.values():
        if not is_free_for_attendee(start, duration, intervals, config):
            return False
    return True


def partition_attendees_at_slot(
    slot_start: datetime,
    duration: timedelta,
    *,
    attendees: list[str],
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    config: OutlookConfig,
) -> tuple[list[str], list[str]]:
    free: list[str] = []
    busy: list[str] = []
    for email in attendees:
        intervals = busy_by_attendee.get(email, [])
        if is_free_for_attendee(slot_start, duration, intervals, config):
            free.append(email)
        else:
            busy.append(email)
    return free, busy


LOW_MOVABILITY_SUBJECT_KEYWORDS = (
    "совет",
    "комитет",
    "правление",
    "1с",
    "board",
    "committee",
)


def movability_score(*, busy_type: str, subject: str) -> str:
    subject_lower = subject.lower()
    if any(keyword in subject_lower for keyword in LOW_MOVABILITY_SUBJECT_KEYWORDS):
        return "low"
    status = busy_type.strip()
    if status == "OOF":
        return "low"
    if status == "Tentative":
        return "high"
    if status in {"Busy", "WorkingElsewhere"}:
        return "medium"
    return "medium"


def conflicting_events_at_slot(
    events: list[Any],
    slot_start: datetime,
    duration: timedelta,
    config: OutlookConfig,
) -> list[dict[str, Any]]:
    local_start = to_local(slot_start, config)
    local_end = local_start + duration
    records: list[dict[str, Any]] = []
    for event in events:
        interval = freebusy_event_interval(event, config)
        if interval is None:
            continue
        event_start, event_end = interval
        if not intervals_overlap(local_start, local_end, event_start, event_end):
            continue
        subject = str(getattr(event, "subject", "") or "").strip()
        busy_type = str(getattr(event, "busy_type", "") or "").strip()
        records.append(
            {
                "event_start": event_start.isoformat(),
                "event_end": event_end.isoformat(),
                "event_subject": subject or None,
                "busy_type": busy_type or None,
                "movability": movability_score(busy_type=busy_type, subject=subject),
            }
        )
    return records


def conflicting_intervals_at_slot(
    busy_intervals: list[tuple[datetime, datetime]],
    slot_start: datetime,
    duration: timedelta,
    config: OutlookConfig,
) -> list[dict[str, Any]]:
    local_start = to_local(slot_start, config)
    local_end = local_start + duration
    records: list[dict[str, Any]] = []
    for busy_start, busy_end in busy_intervals:
        busy_start_local = to_local(busy_start, config)
        busy_end_local = to_local(busy_end, config)
        if not intervals_overlap(local_start, local_end, busy_start_local, busy_end_local):
            continue
        overlap_start = max(local_start, busy_start_local)
        overlap_end = min(local_end, busy_end_local)
        records.append(
            {
                "event_start": overlap_start.isoformat(),
                "event_end": overlap_end.isoformat(),
                "event_subject": None,
                "busy_type": "Busy",
                "movability": "medium",
            }
        )
    return records


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


def normalize_calendar_email(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        return text if "@" in text else None
    mailbox = getattr(value, "mailbox", None)
    if mailbox is not None:
        address = getattr(mailbox, "email_address", None)
        if isinstance(address, str) and "@" in address:
            return address.strip().lower()
    address = getattr(value, "email_address", None)
    if isinstance(address, str) and "@" in address:
        return address.strip().lower()
    return None


def calendar_item_attendee_emails(item: Any) -> list[str]:
    """E-mail участников встречи из EWS CalendarItem."""
    emails: list[str] = []
    for attr in ("required_attendees", "optional_attendees"):
        for entry in getattr(item, attr, None) or []:
            normalized = normalize_calendar_email(entry)
            if normalized:
                emails.append(normalized)
    organizer = normalize_calendar_email(getattr(item, "organizer", None))
    if organizer:
        emails.append(organizer)
    return list(dict.fromkeys(emails))


RESOURCE_CALENDAR_PREFIXES = ("calendar@",)


def _is_resource_calendar_email(email: str) -> bool:
    normalized = email.strip().lower()
    return any(normalized.startswith(prefix) for prefix in RESOURCE_CALENDAR_PREFIXES)


def _human_attendees_for_reschedule_hint(attendee_emails: list[str]) -> list[str]:
    """Участники для групповой проверки альтернативы; без комнат/ресурсных календарей."""
    return [
        email
        for email in attendee_emails
        if email and not _is_resource_calendar_email(email)
    ]


def _apply_blocked_slots_to_busy(
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    *,
    owner_email: str | None,
    blocked_slots: list[tuple[datetime, datetime]],
    config: OutlookConfig,
) -> dict[str, list[tuple[datetime, datetime]]]:
    if not blocked_slots or not owner_email:
        return busy_by_attendee
    updated = dict(busy_by_attendee)
    owner_key = owner_email.strip().lower()
    existing = list(updated.get(owner_key, []))
    updated[owner_key] = coalesce_intervals(existing + blocked_slots, config)
    return updated


def _apply_reserved_slot_to_busy(
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    *,
    owner_email: str | None,
    reserved_slot: tuple[datetime, datetime] | None,
    config: OutlookConfig,
) -> dict[str, list[tuple[datetime, datetime]]]:
    if reserved_slot is None:
        return busy_by_attendee
    return _apply_blocked_slots_to_busy(
        busy_by_attendee,
        owner_email=owner_email,
        blocked_slots=[reserved_slot],
        config=config,
    )


def suggest_reschedule_window(
    *,
    event_start: datetime,
    event_end: datetime,
    busy_intervals: list[tuple[datetime, datetime]],
    config: OutlookConfig,
    step: timedelta,
    search_end: datetime,
    reserved_slot: tuple[datetime, datetime] | None = None,
    blocked_hint_slots: list[tuple[datetime, datetime]] | None = None,
    owner_email: str | None = None,
    meeting_attendees: list[str] | None = None,
) -> tuple[datetime, datetime] | None:
    event_start = to_local(event_start, config)
    event_end = to_local(event_end, config)
    search_end = to_local(search_end, config)
    blocked_slots: list[tuple[datetime, datetime]] = []
    if reserved_slot is not None:
        blocked_slots.append(
            (
                to_local(reserved_slot[0], config),
                to_local(reserved_slot[1], config),
            )
        )
    for slot_start, slot_end in blocked_hint_slots or []:
        blocked_slots.append(
            (
                to_local(slot_start, config),
                to_local(slot_end, config),
            )
        )
    blocked_slots = coalesce_intervals(blocked_slots, config)

    duration = event_end - event_start
    if duration <= timedelta(0):
        duration = timedelta(minutes=30)

    attendee_emails = [
        item.strip().lower()
        for item in (meeting_attendees or [])
        if isinstance(item, str) and item.strip()
    ]
    attendee_emails = list(dict.fromkeys(attendee_emails))
    owner = (owner_email or "").strip().lower() or None
    if owner and owner not in attendee_emails:
        attendee_emails.insert(0, owner)

    group_attendees = _human_attendees_for_reschedule_hint(attendee_emails)
    use_group_check = len(group_attendees) >= 2
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]] | None = None
    if use_group_check:
        fetch_start = event_end
        fetch_end = search_end
        try:
            busy_by_attendee = fetch_busy_intervals_freebusy(
                config,
                group_attendees,
                fetch_start,
                fetch_end,
            )
        except Exception as exc:
            logger.warning(
                "reschedule_hint_group_busy_fetch_failed attendees=%s error=%s",
                len(group_attendees),
                exc,
            )
            busy_by_attendee = None
            use_group_check = False

    if use_group_check and busy_by_attendee is not None:
        busy_by_attendee = _apply_blocked_slots_to_busy(
            busy_by_attendee,
            owner_email=owner,
            blocked_slots=blocked_slots,
            config=config,
        )
        candidate = advance_candidate(event_end, step, duration, config)
        while candidate < search_end:
            if slot_respects_rules(candidate, duration, config) and is_free_for_all(
                candidate,
                duration,
                busy_by_attendee,
                config,
            ):
                return candidate, candidate + duration
            candidate = advance_candidate(candidate, step, duration, config)

    effective_busy = coalesce_intervals(
        list(busy_intervals) + blocked_slots,
        config,
    )
    candidate = advance_candidate(event_end, step, duration, config)
    while candidate < search_end:
        if slot_respects_rules(candidate, duration, config) and is_free_for_attendee(
            candidate,
            duration,
            effective_busy,
            config,
        ):
            return candidate, candidate + duration
        candidate = advance_candidate(candidate, step, duration, config)
    del event_start
    return None


def build_conflict_records(
    *,
    email: str,
    slot_start: datetime,
    duration: timedelta,
    busy_intervals: list[tuple[datetime, datetime]],
    calendar_events: list[Any],
    config: OutlookConfig,
    step: timedelta,
    search_end: datetime,
    max_calendar_items: int = 50,
) -> list[dict[str, Any]]:
    calendar_records: list[dict[str, Any]] = []
    try:
        calendar_items = read_calendar_items_in_range(
            config,
            email,
            range_start=slot_start - timedelta(hours=1),
            range_end=slot_start + duration + timedelta(hours=1),
            max_items=max_calendar_items,
        )
        calendar_records = conflicting_calendar_items_at_slot(
            calendar_items,
            slot_start,
            duration,
            config,
        )
    except Exception:
        calendar_records = []

    freebusy_records = conflicting_events_at_slot(calendar_events, slot_start, duration, config)
    for record in freebusy_records:
        record["source"] = "freebusy"

    interval_records: list[dict[str, Any]] = []
    if not calendar_records and not freebusy_records:
        interval_records = conflicting_intervals_at_slot(
            busy_intervals,
            slot_start,
            duration,
            config,
        )
        for record in interval_records:
            record["source"] = "interval"

    merged_records = dedupe_conflict_records(
        calendar_records + freebusy_records + interval_records
    )
    reserved_slot = (slot_start, slot_start + duration)
    conflicts: list[dict[str, Any]] = []
    assigned_hints: list[tuple[datetime, datetime]] = []
    for record in merged_records:
        event_start = datetime.fromisoformat(record["event_start"])
        event_end = datetime.fromisoformat(record["event_end"])
        hint = suggest_reschedule_window(
            event_start=event_start,
            event_end=event_end,
            busy_intervals=busy_intervals,
            config=config,
            step=step,
            search_end=search_end,
            reserved_slot=reserved_slot,
            blocked_hint_slots=assigned_hints,
            owner_email=email,
            meeting_attendees=record.get("event_attendees"),
        )
        if hint is not None:
            assigned_hints.append(hint)
        meeting_attendees = list(record.get("event_attendees") or [])
        if email.strip().lower() not in {item.lower() for item in meeting_attendees}:
            meeting_attendees.insert(0, email.strip().lower())
        subject = str(record.get("event_subject") or "")
        busy_type = str(record.get("busy_type") or "")
        source = record.get("source") or "interval"
        if source not in {"calendar", "freebusy", "interval", "company_calendar"}:
            source = "interval"
        conflicts.append(
            {
                "email": email,
                "event_start": record["event_start"],
                "event_end": record["event_end"],
                "event_subject": record.get("event_subject"),
                "busy_type": record.get("busy_type"),
                "movability": record.get("movability") or "medium",
                "movability_reason": movability_reason(
                    busy_type=busy_type,
                    subject=subject,
                    source=source,
                ),
                "source": source,
                "event_attendees": meeting_attendees,
                "can_auto_reschedule": False,
                "reschedule_hint_start": hint[0].isoformat() if hint else None,
                "reschedule_hint_end": hint[1].isoformat() if hint else None,
            }
        )
    return conflicts


def movability_reason(
    *,
    busy_type: str,
    subject: str,
    source: Literal["calendar", "freebusy", "interval", "company_calendar"],
) -> str:
    subject_lower = subject.lower()
    if any(keyword in subject_lower for keyword in LOW_MOVABILITY_SUBJECT_KEYWORDS):
        return "protected_subject"
    status = busy_type.strip()
    if status == "OOF":
        return "oof"
    if status == "Tentative":
        return "tentative"
    if source == "interval":
        return "unknown_interval"
    if status in {"Busy", "WorkingElsewhere"}:
        return "busy"
    return "busy"


def conflicting_calendar_items_at_slot(
    items: list[Any],
    slot_start: datetime,
    duration: timedelta,
    config: OutlookConfig,
) -> list[dict[str, Any]]:
    local_start = to_local(slot_start, config)
    local_end = local_start + duration
    records: list[dict[str, Any]] = []
    for item in items:
        interval = event_interval(item, config)
        if interval is None:
            continue
        event_start, event_end = interval
        if not intervals_overlap(local_start, local_end, event_start, event_end):
            continue
        subject = str(getattr(item, "subject", "") or "").strip()
        busy_type = str(getattr(item, "legacy_free_busy_status", "") or "").strip()
        organizer = None
        organizer_obj = getattr(item, "organizer", None)
        if organizer_obj is not None:
            organizer = getattr(organizer_obj, "email_address", None) or str(organizer_obj)
        records.append(
            {
                "event_start": event_start.isoformat(),
                "event_end": event_end.isoformat(),
                "event_subject": subject or None,
                "busy_type": busy_type or None,
                "organizer": organizer,
                "event_attendees": calendar_item_attendee_emails(item),
                "movability": movability_score(busy_type=busy_type, subject=subject),
                "source": "calendar",
            }
        )
    return records


def dedupe_conflict_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Один интервал — одна запись; при дубле оставляем запись с темой (calendar > freebusy > interval)."""
    source_rank = {"calendar": 0, "freebusy": 1, "interval": 2}
    by_interval: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []

    def should_replace(existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        new_subject = str(candidate.get("event_subject") or "").strip()
        old_subject = str(existing.get("event_subject") or "").strip()
        if new_subject and not old_subject:
            return True
        if old_subject and not new_subject:
            return False
        new_rank = source_rank.get(str(candidate.get("source") or "interval"), 2)
        old_rank = source_rank.get(str(existing.get("source") or "interval"), 2)
        return new_rank < old_rank

    for record in records:
        key = (
            str(record.get("event_start") or ""),
            str(record.get("event_end") or ""),
        )
        if not key[0] or not key[1]:
            continue
        if key not in by_interval:
            order.append(key)
            by_interval[key] = record
            continue
        if should_replace(by_interval[key], record):
            by_interval[key] = record
        elif record.get("event_attendees") and not by_interval[key].get("event_attendees"):
            by_interval[key]["event_attendees"] = record["event_attendees"]

    return [by_interval[key] for key in order]


def attach_reschedule_hints(
    records: list[dict[str, Any]],
    *,
    owner_email: str,
    busy_intervals: list[tuple[datetime, datetime]],
    config: OutlookConfig,
    step: timedelta,
    search_end: datetime,
    reserved_slot: tuple[datetime, datetime] | None = None,
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    assigned_hints: list[tuple[datetime, datetime]] = []
    for record in records:
        event_start = datetime.fromisoformat(record["event_start"])
        event_end = datetime.fromisoformat(record["event_end"])
        hint = suggest_reschedule_window(
            event_start=event_start,
            event_end=event_end,
            busy_intervals=busy_intervals,
            config=config,
            step=step,
            search_end=search_end,
            reserved_slot=reserved_slot,
            blocked_hint_slots=assigned_hints,
            owner_email=owner_email,
            meeting_attendees=record.get("event_attendees"),
        )
        if hint is not None:
            assigned_hints.append(hint)
        subject = str(record.get("event_subject") or "")
        busy_type = str(record.get("busy_type") or "")
        source = record.get("source") or "interval"
        if source not in {"calendar", "freebusy", "interval", "company_calendar"}:
            source = "interval"
        conflicts.append(
            {
                **record,
                "movability": record.get("movability") or "medium",
                "movability_reason": movability_reason(
                    busy_type=busy_type,
                    subject=subject,
                    source=source,
                ),
                "can_auto_reschedule": False,
                "reschedule_hint_start": hint[0].isoformat() if hint else None,
                "reschedule_hint_end": hint[1].isoformat() if hint else None,
            }
        )
    return conflicts


def build_slot_participant_details(
    *,
    config: OutlookConfig,
    attendees: list[dict[str, Any]],
    slot_start: datetime,
    slot_end: datetime,
    step_minutes: int = 15,
    max_calendar_items: int = 50,
    source: AvailabilitySource = "freebusy",
    max_items: int = 500,
    workers: int = 4,
) -> dict[str, Any]:
    """Статус каждого участника в выбранном слоте: свободен/занят и мешающие встречи."""
    duration = slot_end - slot_start
    if duration <= timedelta(0):
        raise ValueError("slot_end должно быть позже slot_start")

    attendee_emails = [
        str(item.get("email") or "").strip()
        for item in attendees
        if str(item.get("email") or "").strip()
    ]
    step = timedelta(minutes=max(step_minutes, 1))
    window_start = slot_start - timedelta(hours=1)
    window_end = slot_end + timedelta(hours=1)
    hint_search_end = min(
        slot_end + timedelta(days=3),
        slot_start + timedelta(days=30),
    )

    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]] = {}
    if attendee_emails:
        busy_by_attendee = fetch_all_busy_intervals(
            config,
            attendee_emails,
            window_start,
            window_end,
            source=source,
            max_items=max_items,
            workers=workers,
        )

    conflict_events = fetch_freebusy_calendar_events(
        config,
        attendee_emails,
        window_start,
        window_end,
    ) if attendee_emails else {}

    participants: list[dict[str, Any]] = []
    for attendee in attendees:
        email = str(attendee.get("email") or "").strip()
        fio = str(attendee.get("fio") or "").strip() or email or "—"
        role = str(attendee.get("role") or "participant").strip()
        if not email:
            participants.append(
                {
                    "fio": fio,
                    "email": None,
                    "role": role,
                    "status": "unknown",
                    "blocking_events": [],
                    "calendar_access_error": "E-mail участника не найден",
                }
            )
            continue

        busy_intervals = busy_by_attendee.get(email, [])
        if is_free_for_attendee(slot_start, duration, busy_intervals, config):
            participants.append(
                {
                    "fio": fio,
                    "email": email,
                    "role": role,
                    "status": "free",
                    "blocking_events": [],
                    "calendar_access_error": None,
                }
            )
            continue

        calendar_error: str | None = None
        calendar_records: list[dict[str, Any]] = []
        try:
            calendar_items = read_calendar_items_in_range(
                config,
                email,
                range_start=window_start,
                range_end=window_end,
                max_items=max_calendar_items,
            )
            calendar_records = conflicting_calendar_items_at_slot(
                calendar_items,
                slot_start,
                duration,
                config,
            )
        except Exception as exc:
            calendar_error = str(exc).strip() or "Не удалось прочитать календарь участника"

        freebusy_records = conflicting_events_at_slot(
            conflict_events.get(email, []),
            slot_start,
            duration,
            config,
        )
        for record in freebusy_records:
            record["source"] = "freebusy"

        interval_records: list[dict[str, Any]] = []
        if not calendar_records and not freebusy_records:
            interval_records = conflicting_intervals_at_slot(
                busy_intervals,
                slot_start,
                duration,
                config,
            )
            for record in interval_records:
                record["source"] = "interval"

        merged_records = dedupe_conflict_records(calendar_records + freebusy_records + interval_records)

        blocking_events = attach_reschedule_hints(
            merged_records,
            owner_email=email,
            busy_intervals=busy_intervals,
            config=config,
            step=step,
            search_end=hint_search_end,
            reserved_slot=(slot_start, slot_end),
        )
        for event in blocking_events:
            event["email"] = email

        participants.append(
            {
                "fio": fio,
                "email": email,
                "role": role,
                "status": "busy",
                "blocking_events": blocking_events,
                "calendar_access_error": calendar_error,
            }
        )

    return {
        "slot_start": slot_start.isoformat(),
        "slot_end": slot_end.isoformat(),
        "duration_minutes": int(duration.total_seconds() // 60),
        "participants": participants,
    }


def iterate_slot_candidates(
    earliest_allowed: datetime,
    search_end: datetime,
    *,
    duration: timedelta,
    step: timedelta,
    config: OutlookConfig,
) -> Iterator[datetime]:
    candidate = max(earliest_allowed, align_preferred(earliest_allowed, config))
    safety_limit = max(
        1000,
        int((search_end - earliest_allowed).total_seconds() // max(step.total_seconds(), 60)) + 50,
    )
    emitted = 0
    while candidate < search_end and emitted < safety_limit:
        if slot_respects_rules(candidate, duration, config):
            yield candidate
            emitted += 1
        candidate = advance_candidate(candidate, step, duration, config)


def quorum_confidence(*, coverage_ratio: float, required_ok: bool, verified: bool, conflicts: int) -> float:
    if not required_ok:
        return 0.5
    confidence = 0.55 + coverage_ratio * 0.35
    if verified:
        confidence += 0.05
    if conflicts == 0:
        confidence += 0.05
    return min(confidence, 0.99)


def coverage_ratios(
    free_attendees: list[str],
    attendees: list[str],
    attendee_weights: dict[str, float] | None,
) -> tuple[float, float]:
    """Возвращает (weighted_ratio, flat_ratio)."""
    attendee_set = set(attendees)
    flat_ratio = len(free_attendees) / len(attendees) if attendees else 0.0
    if not attendee_weights:
        return flat_ratio, flat_ratio
    total_weight = sum(attendee_weights.get(email, 1.0) for email in attendees)
    if total_weight <= 0:
        return flat_ratio, flat_ratio
    free_weight = sum(
        attendee_weights.get(email, 1.0)
        for email in free_attendees
        if email in attendee_set
    )
    return free_weight / total_weight, flat_ratio


MOVABILITY_RESCHEDULE_PENALTY: dict[str, float] = {
    "high": 0.5,
    "medium": 1.0,
    "low": 2.5,
}
IMPACT_COVERAGE_WEIGHT = 1.0
IMPACT_BUSY_ATTENDEE_WEIGHT = 0.2
IMPACT_LEADERSHIP_BUSY = 5.0
IMPACT_REQUIRED_FAIL = 15.0
IMPACT_CONFLICT_WEIGHT = 0.25
QUORUM_RANK_SHORTLIST_MULTIPLIER = 5


def busy_attendee_weight_cost(
    busy_attendees: list[str],
    attendee_weights: dict[str, float] | None,
) -> float:
    if not busy_attendees:
        return 0.0
    if not attendee_weights:
        return float(len(busy_attendees))
    return sum(attendee_weights.get(email, 1.0) for email in busy_attendees)


def conflict_reschedule_cost(
    conflicts: list[dict[str, Any]],
    attendee_weights: dict[str, float] | None,
) -> float:
    total = 0.0
    for conflict in conflicts:
        email = str(conflict.get("email") or "")
        weight = attendee_weights.get(email, 1.0) if attendee_weights else 1.0
        movability = str(conflict.get("movability") or "medium")
        penalty = MOVABILITY_RESCHEDULE_PENALTY.get(movability, 1.0)
        total += weight * penalty
    return total


def preliminary_slot_impact(
    *,
    score_ratio: float,
    busy_attendees: list[str],
    required: list[str],
    required_ok: bool,
    attendee_weights: dict[str, float] | None,
) -> float:
    """Меньше — лучше. Быстрая оценка до построения conflicts."""
    impact = (1.0 - score_ratio) * IMPACT_COVERAGE_WEIGHT
    impact += busy_attendee_weight_cost(busy_attendees, attendee_weights) * IMPACT_BUSY_ATTENDEE_WEIGHT
    required_set = set(required)
    impact += sum(1 for email in busy_attendees if email in required_set) * IMPACT_LEADERSHIP_BUSY
    if not required_ok:
        impact += IMPACT_REQUIRED_FAIL
    return round(impact, 4)


def slot_impact_score(
    *,
    weighted_coverage_ratio: float,
    required_ok: bool,
    busy_attendees: list[str],
    required: list[str],
    conflicts: list[dict[str, Any]],
    attendee_weights: dict[str, float] | None,
) -> float:
    """Меньше — лучше. Учитывает покрытие, должности занятых и переносимость конфликтов."""
    impact = (1.0 - weighted_coverage_ratio) * IMPACT_COVERAGE_WEIGHT
    impact += conflict_reschedule_cost(conflicts, attendee_weights) * IMPACT_CONFLICT_WEIGHT
    required_set = set(required)
    impact += sum(1 for email in busy_attendees if email in required_set) * IMPACT_LEADERSHIP_BUSY
    if not required_ok:
        impact += IMPACT_REQUIRED_FAIL
    return round(impact, 4)


def count_low_movability_conflicts(conflicts: list[dict[str, Any]]) -> int:
    return sum(1 for conflict in conflicts if str(conflict.get("movability") or "") == "low")


def count_easy_reschedule_conflicts(conflicts: list[dict[str, Any]]) -> int:
    return sum(1 for conflict in conflicts if str(conflict.get("movability") or "") == "high")


def quorum_search_start(preferred: datetime, config: OutlookConfig) -> datetime:
    """Начало перебора: с 08:00 дня желаемой даты, а не с preferred (10:00 пропускает 09:00–12:00)."""
    requested = to_local(preferred, config).replace(second=0, microsecond=0)
    if not is_workday(requested, config):
        return max(align_preferred(requested, config), not_before_now(config))
    return max(combine(requested, WORK_START, config), not_before_now(config))


def _slot_preference_distance(
    slot_start: datetime,
    preferred: datetime,
    config: OutlookConfig,
) -> float:
    return abs((to_local(slot_start, config) - to_local(preferred, config)).total_seconds())


def _quorum_pool_sort_key(
    item: dict[str, Any],
    *,
    preferred: datetime,
    config: OutlookConfig,
) -> tuple[float, float, float, datetime]:
    slot_start: datetime = item["slot_start"]
    return (
        item["preliminary_impact"],
        -item["score_ratio"],
        _slot_preference_distance(slot_start, preferred, config),
        slot_start,
    )


def _quorum_candidate_sort_key(
    item: dict[str, Any],
    *,
    preferred: datetime,
    config: OutlookConfig,
) -> tuple[int, int, float, float, str]:
    slot_start = datetime.fromisoformat(item["slot_start"])
    reschedule_count = int(item.get("reschedule_count") or 0)
    easy_count = int(item.get("easy_reschedule_count") or 0)
    hard_reschedules = max(reschedule_count - easy_count, 0)
    return (
        int(item.get("low_movability_count") or 0),
        hard_reschedules,
        _slot_preference_distance(slot_start, preferred, config),
        float(item.get("impact_score") or 999.0),
        item["slot_start"],
    )


def _build_quorum_candidate_payload(
    *,
    item: dict[str, Any],
    attendees: list[str],
    required: list[str],
    required_set: set[str],
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    attendee_weights: dict[str, float] | None,
    config: OutlookConfig,
    duration: timedelta,
    step: timedelta,
    search_end: datetime,
    verified: bool,
    free_attendees: list[str],
    busy_attendees: list[str],
) -> dict[str, Any]:
    slot_start: datetime = item["slot_start"]
    slot_end: datetime = item["slot_end"]
    conflict_window_end = min(search_end, slot_end + timedelta(days=3))
    conflict_events = fetch_freebusy_calendar_events(
        config,
        busy_attendees,
        slot_start - timedelta(hours=1),
        slot_end + timedelta(hours=1),
    ) if busy_attendees else {}

    conflicts: list[dict[str, Any]] = []
    for email in busy_attendees:
        conflicts.extend(
            build_conflict_records(
                email=email,
                slot_start=slot_start,
                duration=duration,
                busy_intervals=busy_by_attendee.get(email, []),
                calendar_events=conflict_events.get(email, []),
                config=config,
                step=step,
                search_end=conflict_window_end,
            )
        )

    weighted_ratio, _flat_ratio = coverage_ratios(
        free_attendees,
        attendees,
        attendee_weights,
    )
    required_ok = all(email in free_attendees for email in required_set)
    impact_score = slot_impact_score(
        weighted_coverage_ratio=weighted_ratio,
        required_ok=required_ok,
        busy_attendees=busy_attendees,
        required=required,
        conflicts=conflicts,
        attendee_weights=attendee_weights,
    )
    return {
        "slot_start": slot_start.isoformat(),
        "slot_end": slot_end.isoformat(),
        "duration_minutes": int(duration.total_seconds() // 60),
        "coverage": {
            "free": len(free_attendees),
            "total": len(attendees),
            "ratio": round(len(free_attendees) / len(attendees), 4),
            "weighted_ratio": round(weighted_ratio, 4),
            "required_ok": required_ok,
        },
        "free_attendees": free_attendees,
        "busy_attendees": busy_attendees,
        "conflicts": conflicts,
        "verified": verified,
        "impact_score": impact_score,
        "busy_weight_cost": round(
            busy_attendee_weight_cost(busy_attendees, attendee_weights),
            4,
        ),
        "reschedule_count": len(conflicts),
        "easy_reschedule_count": count_easy_reschedule_conflicts(conflicts),
        "low_movability_count": count_low_movability_conflicts(conflicts),
        "confidence": quorum_confidence(
            coverage_ratio=weighted_ratio if attendee_weights else len(free_attendees) / len(attendees),
            required_ok=required_ok,
            verified=verified,
            conflicts=len(conflicts),
        ),
    }


COMPANY_CALENDAR_CHUNK_HOURS = 4
COMPANY_CALENDAR_MAX_CANDIDATES = 10
COMPANY_CALENDAR_MAX_ITEMS_PER_CHUNK = 100
COMPANY_CALENDAR_STEP_MINUTES = 15
MOVABILITY_SORT_RANK = {"high": 0, "medium": 1, "low": 2}


def _human_calendar_attendee_emails(item: Any) -> list[str]:
    return [
        email
        for email in calendar_item_attendee_emails(item)
        if email and not _is_resource_calendar_email(email)
    ]


def _iter_company_calendar_windows(
    search_start: datetime,
    search_end: datetime,
    *,
    config: OutlookConfig,
    chunk_hours: int = COMPANY_CALENDAR_CHUNK_HOURS,
) -> Iterator[tuple[datetime, datetime]]:
    cursor = to_local(search_start, config)
    end = to_local(search_end, config)
    step = timedelta(hours=max(chunk_hours, 1))
    while cursor < end:
        window_end = min(cursor + step, end)
        yield cursor, window_end
        cursor = window_end


def _meeting_attendees_in_event(
    item: Any,
    attendee_set: set[str],
) -> list[str]:
    return [
        email
        for email in _human_calendar_attendee_emails(item)
        if email in attendee_set
    ]


def _event_blocks_target_or_busy(
    event_start: datetime,
    event_end: datetime,
    *,
    target_start: datetime,
    target_end: datetime,
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    matched_attendees: list[str],
    config: OutlookConfig,
) -> bool:
    if intervals_overlap(event_start, event_end, target_start, target_end):
        return True
    for email in matched_attendees:
        for busy_start, busy_end in busy_by_attendee.get(email, []):
            busy_start_local = to_local(busy_start, config)
            busy_end_local = to_local(busy_end, config)
            if intervals_overlap(event_start, event_end, busy_start_local, busy_end_local):
                return True
    return False


def _pick_primary_blocked_attendee(
    matched_attendees: list[str],
    *,
    attendee_weights: dict[str, float] | None,
) -> str:
    if not matched_attendees:
        return ""
    weights = attendee_weights or {}
    return max(matched_attendees, key=lambda email: weights.get(email, 1.0))


def _company_calendar_candidate_sort_key(
    record: dict[str, Any],
    *,
    target_start: datetime,
    config: OutlookConfig,
) -> tuple[int, int, float]:
    movability = str(record.get("movability") or "medium")
    movability_rank = MOVABILITY_SORT_RANK.get(movability, 1)
    required_hits = int(record.get("required_attendee_hits") or 0)
    event_start_raw = record.get("event_start")
    distance = 0.0
    if event_start_raw:
        event_start = datetime.fromisoformat(str(event_start_raw))
        distance = abs((to_local(event_start, config) - target_start).total_seconds())
    return (movability_rank, required_hits, distance)


def find_company_calendar_reschedule_candidates(
    *,
    attendee_emails: list[str],
    required_attendee_emails: list[str] | None = None,
    planned_start: datetime,
    duration: timedelta,
    max_days: int,
    attendee_weights: dict[str, float] | None = None,
    max_results: int = COMPANY_CALENDAR_MAX_CANDIDATES,
    config: OutlookConfig | None = None,
) -> dict[str, Any]:
    """Кандидаты на перенос из общего календаря компании при полном отсутствии слота."""
    config = config or load_config()
    company_calendar = (config.company_calendar or "").strip().lower()
    if not company_calendar:
        return {
            "company_calendar": None,
            "candidates": [],
            "events_scanned": 0,
            "search_window": None,
        }

    normalized_attendees = [
        email.strip().lower()
        for email in attendee_emails
        if isinstance(email, str) and email.strip()
    ]
    attendee_set = set(dict.fromkeys(normalized_attendees))
    if not attendee_set:
        return {
            "company_calendar": company_calendar,
            "candidates": [],
            "events_scanned": 0,
            "search_window": None,
        }

    required_set = {
        email.strip().lower()
        for email in (required_attendee_emails or normalized_attendees)
        if email.strip()
    }
    target_start = to_local(planned_start, config).replace(second=0, microsecond=0)
    if duration <= timedelta(0):
        duration = timedelta(minutes=30)
    target_end = target_start + duration
    search_start = quorum_search_start(target_start, config)
    search_end = search_start + timedelta(days=max(max_days, 1))
    step = timedelta(minutes=COMPANY_CALENDAR_STEP_MINUTES)

    busy_by_attendee = fetch_busy_intervals_freebusy(
        config,
        list(attendee_set),
        search_start,
        search_end,
    )

    records_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    events_scanned = 0

    for window_start, window_end in _iter_company_calendar_windows(
        search_start,
        search_end,
        config=config,
    ):
        try:
            items = read_calendar_items_in_range(
                config,
                company_calendar,
                range_start=window_start,
                range_end=window_end,
                max_items=COMPANY_CALENDAR_MAX_ITEMS_PER_CHUNK,
            )
        except Exception as exc:
            logger.warning(
                "company_calendar_chunk_failed window=%s..%s error=%s",
                window_start.isoformat(),
                window_end.isoformat(),
                exc,
            )
            continue

        for item in items:
            events_scanned += 1
            interval = event_interval(item, config)
            if interval is None:
                continue
            event_start, event_end = interval
            matched_attendees = _meeting_attendees_in_event(item, attendee_set)
            if not matched_attendees:
                continue
            if not _event_blocks_target_or_busy(
                event_start,
                event_end,
                target_start=target_start,
                target_end=target_end,
                busy_by_attendee=busy_by_attendee,
                matched_attendees=matched_attendees,
                config=config,
            ):
                continue

            subject = str(getattr(item, "subject", "") or "").strip()
            busy_type = str(getattr(item, "legacy_free_busy_status", "") or "").strip()
            organizer = None
            organizer_obj = getattr(item, "organizer", None)
            if organizer_obj is not None:
                organizer = getattr(organizer_obj, "email_address", None) or str(organizer_obj)
            primary_email = _pick_primary_blocked_attendee(
                matched_attendees,
                attendee_weights=attendee_weights,
            )
            primary_busy = busy_by_attendee.get(primary_email, [])
            hint = suggest_reschedule_window(
                event_start=event_start,
                event_end=event_end,
                busy_intervals=primary_busy,
                config=config,
                step=step,
                search_end=search_end,
                reserved_slot=(target_start, target_end),
                owner_email=primary_email,
                meeting_attendees=matched_attendees,
            )
            record = {
                "email": primary_email,
                "event_start": event_start.isoformat(),
                "event_end": event_end.isoformat(),
                "event_subject": subject or None,
                "busy_type": busy_type or None,
                "organizer": organizer,
                "event_attendees": matched_attendees,
                "required_attendee_hits": sum(
                    1 for email in matched_attendees if email in required_set
                ),
                "movability": movability_score(busy_type=busy_type, subject=subject),
                "movability_reason": movability_reason(
                    busy_type=busy_type,
                    subject=subject,
                    source="company_calendar",
                ),
                "source": "company_calendar",
                "can_auto_reschedule": False,
                "reschedule_hint_start": hint[0].isoformat() if hint else None,
                "reschedule_hint_end": hint[1].isoformat() if hint else None,
            }
            dedupe_key = (
                record["event_start"],
                record["event_end"],
                str(record.get("event_subject") or ""),
            )
            existing = records_by_key.get(dedupe_key)
            if existing is None or record["required_attendee_hits"] > int(
                existing.get("required_attendee_hits") or 0
            ):
                records_by_key[dedupe_key] = record

    candidates = sorted(
        records_by_key.values(),
        key=lambda item: _company_calendar_candidate_sort_key(
            item,
            target_start=target_start,
            config=config,
        ),
    )[: max(max_results, 1)]

    return {
        "company_calendar": company_calendar,
        "candidates": candidates,
        "events_scanned": events_scanned,
        "search_window": {
            "start": search_start.isoformat(),
            "end": search_end.isoformat(),
            "target_start": target_start.isoformat(),
            "target_end": target_end.isoformat(),
        },
    }


def find_quorum_slots(
    *,
    config: OutlookConfig,
    attendees: list[str],
    preferred: datetime,
    duration: timedelta,
    max_days: int,
    step: timedelta,
    max_items: int,
    source: AvailabilitySource = "freebusy",
    workers: int = 4,
    required_attendees: list[str] | None = None,
    attendee_weights: dict[str, float] | None = None,
    min_coverage_ratio: float = 0.7,
    max_results: int = 3,
    verify_top_n: int = 3,
    verify_calendar: bool = True,
) -> dict[str, Any]:
    if not attendees:
        raise ValueError("Укажите хотя бы одного участника (--attendee).")
    if duration <= timedelta(0):
        raise ValueError("Длительность должна быть больше 0.")
    if max_days < 1:
        raise ValueError("--max-days должно быть >= 1.")
    if not 0 < min_coverage_ratio <= 1:
        raise ValueError("min_coverage_ratio должно быть в диапазоне (0, 1].")

    required = [email for email in (required_attendees or attendees) if email in attendees]
    if not required:
        required = list(attendees)

    requested = to_local(preferred, config).replace(second=0, microsecond=0)
    earliest_allowed = quorum_search_start(preferred, config)
    search_end = earliest_allowed + timedelta(days=max_days)

    busy_by_attendee = fetch_all_busy_intervals(
        config,
        attendees,
        earliest_allowed,
        search_end,
        source=source,
        max_items=max_items,
        workers=workers,
    )

    checked = 0
    scored: list[dict[str, Any]] = []
    scored_fallback: list[dict[str, Any]] = []
    required_set = set(required)
    with timed_step("scan.quorum_slots", max_days=max_days, step_minutes=int(step.total_seconds() // 60)):
        for candidate in iterate_slot_candidates(
            earliest_allowed,
            search_end,
            duration=duration,
            step=step,
            config=config,
        ):
            checked += 1
            free_attendees, busy_attendees = partition_attendees_at_slot(
                candidate,
                duration,
                attendees=attendees,
                busy_by_attendee=busy_by_attendee,
                config=config,
            )
            if not free_attendees:
                continue
            required_ok = all(email in free_attendees for email in required_set)
            weighted_ratio, flat_ratio = coverage_ratios(
                free_attendees,
                attendees,
                attendee_weights,
            )
            score_ratio = weighted_ratio if attendee_weights else flat_ratio
            payload = {
                "slot_start": candidate,
                "slot_end": candidate + duration,
                "free_attendees": free_attendees,
                "busy_attendees": busy_attendees,
                "coverage_ratio": flat_ratio,
                "weighted_coverage_ratio": weighted_ratio,
                "score_ratio": score_ratio,
                "required_ok": required_ok,
                "preliminary_impact": preliminary_slot_impact(
                    score_ratio=score_ratio,
                    busy_attendees=busy_attendees,
                    required=required,
                    required_ok=required_ok,
                    attendee_weights=attendee_weights,
                ),
            }
            scored_fallback.append(payload)
            if not required_ok or score_ratio < min_coverage_ratio:
                continue
            scored.append(payload)

    use_fallback = not scored
    pool = scored_fallback if use_fallback else scored
    pool.sort(key=lambda item: _quorum_pool_sort_key(item, preferred=requested, config=config))
    shortlist_size = min(
        len(pool),
        max(max_results * QUORUM_RANK_SHORTLIST_MULTIPLIER, max_results + 10),
    )
    shortlisted = pool[: max(shortlist_size, 1)]

    verify_count = max(verify_top_n, 0) if verify_calendar else 0
    candidates: list[dict[str, Any]] = []
    reschedule_assisted = False

    def append_candidate(
        item: dict[str, Any],
        *,
        index: int,
        allow_required_failures: bool,
    ) -> None:
        slot_start: datetime = item["slot_start"]
        verified = False
        free_attendees = list(item["free_attendees"])
        busy_attendees = list(item["busy_attendees"])
        if index < verify_count:
            calendar_ok, calendar_busy = verify_slot_with_calendar(
                config=config,
                attendees=attendees,
                slot_start=slot_start,
                duration=duration,
                max_items=max_items,
                workers=workers,
            )
            verified = calendar_ok
            free_attendees, busy_attendees = partition_attendees_at_slot(
                slot_start,
                duration,
                attendees=attendees,
                busy_by_attendee=calendar_busy,
                config=config,
            )
            if not allow_required_failures and not all(email in free_attendees for email in required_set):
                return
            if not free_attendees:
                return
        candidates.append(
            _build_quorum_candidate_payload(
                item=item,
                attendees=attendees,
                required=required,
                required_set=required_set,
                busy_by_attendee=busy_by_attendee,
                attendee_weights=attendee_weights,
                config=config,
                duration=duration,
                step=step,
                search_end=search_end,
                verified=verified,
                free_attendees=free_attendees,
                busy_attendees=busy_attendees,
            )
        )

    for index, item in enumerate(shortlisted):
        append_candidate(item, index=index, allow_required_failures=use_fallback)

    if not candidates and scored_fallback:
        reschedule_pool = sorted(
            scored_fallback,
            key=lambda item: (
                item["preliminary_impact"],
                -item["score_ratio"],
                _slot_preference_distance(item["slot_start"], requested, config),
            ),
        )
        for index, item in enumerate(reschedule_pool[: max(shortlist_size, 1)]):
            append_candidate(item, index=index, allow_required_failures=True)
        reschedule_assisted = bool(candidates)
        use_fallback = use_fallback or reschedule_assisted

    candidates.sort(key=lambda item: _quorum_candidate_sort_key(item, preferred=requested, config=config))
    candidates = candidates[: max(max_results, 1)]

    if not candidates:
        raise RuntimeError(
            f"Quorum-слот не найден: min_coverage={min_coverage_ratio:.0%}, "
            f"required={len(required)}, search_days={max_days}."
        )

    return {
        "preferred": requested.isoformat(),
        "earliest_allowed": earliest_allowed.isoformat(),
        "search_until": search_end.isoformat(),
        "min_coverage_ratio": min_coverage_ratio,
        "required_attendees": required,
        "attendees": attendees,
        "checked_candidates": checked,
        "availability_source": source,
        "search_mode": (
            "reschedule_assisted"
            if reschedule_assisted
            else ("quorum_fallback" if use_fallback else "quorum")
        ),
        "partial_fallback": use_fallback,
        "candidates": candidates,
    }


def advance_candidate(
    current: datetime,
    step: timedelta,
    duration: timedelta,
    config: OutlookConfig,
) -> datetime:
    current = to_local(current, config) + step

    while True:
        if not is_workday(current, config):
            current = next_workday_start(current - timedelta(days=1), config)
            continue

        latest_start = combine(current, WORK_END, config) - duration
        if current.time() < WORK_START:
            current = combine(current, WORK_START, config)
            continue
        if current > latest_start:
            current = next_workday_start(current, config)
            continue
        return current


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


def union_busy_for_all(
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    config: OutlookConfig,
    range_start: datetime,
    range_end: datetime,
) -> list[tuple[datetime, datetime]]:
    """Объединение занятости всех участников: занято, если занят хотя бы один."""
    all_intervals: list[tuple[datetime, datetime]] = []
    for intervals in busy_by_attendee.values():
        all_intervals.extend(intervals)
    return coalesce_intervals(
        all_intervals,
        config,
        clip_start=range_start,
        clip_end=range_end,
    )


def first_valid_slot_in_window(
    window_start: datetime,
    window_end: datetime,
    *,
    duration: timedelta,
    step: timedelta,
    config: OutlookConfig,
) -> tuple[datetime | None, int]:
    """Первый слот в свободном окне [window_start, window_end)."""
    local_start = to_local(window_start, config)
    local_end = to_local(window_end, config)
    if local_end <= local_start:
        return None, 0

    checked = 0
    candidate = max(local_start, align_preferred(local_start, config))
    while candidate < local_end and candidate + duration <= local_end:
        if candidate < local_start:
            candidate = max(local_start, align_preferred(local_start, config))
            continue
        checked += 1
        if slot_respects_rules(candidate, duration, config):
            return candidate, checked
        candidate = advance_candidate(candidate, step, duration, config)
    return None, checked


def find_slot_via_busy_gaps(
    *,
    earliest_allowed: datetime,
    search_end: datetime,
    duration: timedelta,
    step: timedelta,
    union_busy: list[tuple[datetime, datetime]],
    config: OutlookConfig,
) -> tuple[datetime | None, int]:
    """Ищет слот в промежутках между объединёнными блоками занятости."""
    checked = 0
    window_start = earliest_allowed

    for busy_start, busy_end in union_busy:
        if busy_start > search_end:
            break
        if busy_start > window_start:
            slot, window_checked = first_valid_slot_in_window(
                window_start,
                min(busy_start, search_end),
                duration=duration,
                step=step,
                config=config,
            )
            checked += window_checked
            if slot is not None:
                return slot, checked
        window_start = max(window_start, busy_end)

    if window_start < search_end:
        slot, window_checked = first_valid_slot_in_window(
            window_start,
            search_end,
            duration=duration,
            step=step,
            config=config,
        )
        checked += window_checked
        if slot is not None:
            return slot, checked
    return None, checked


def merge_busy_intervals(
    *sources: dict[str, list[tuple[datetime, datetime]]],
) -> dict[str, list[tuple[datetime, datetime]]]:
    merged: dict[str, list[tuple[datetime, datetime]]] = {}
    for source in sources:
        for email, intervals in source.items():
            bucket = merged.setdefault(email, [])
            bucket.extend(intervals)
    return merged


def verify_slot_with_calendar(
    *,
    config: OutlookConfig,
    attendees: list[str],
    slot_start: datetime,
    duration: timedelta,
    max_items: int,
    workers: int,
) -> tuple[bool, dict[str, list[tuple[datetime, datetime]]]]:
    del max_items, workers
    window_start = slot_start - timedelta(hours=2)
    window_end = slot_start + duration + timedelta(hours=2)
    with timed_step("verify.freebusy_events", attendees=len(attendees)):
        busy_by_attendee = fetch_busy_intervals_freebusy_events(
            config,
            attendees,
            window_start,
            window_end,
        )
    return is_free_for_all(slot_start, duration, busy_by_attendee, config), busy_by_attendee


def _slot_search_result(
    *,
    requested: datetime,
    earliest_allowed: datetime,
    candidate: datetime,
    duration: timedelta,
    attendees: list[str],
    checked: int,
    search_end: datetime,
    source: AvailabilitySource,
) -> dict[str, Any]:
    end = candidate + duration
    return {
        "preferred": requested.isoformat(),
        "earliest_allowed": earliest_allowed.isoformat(),
        "slot_start": candidate.isoformat(),
        "slot_end": end.isoformat(),
        "duration_minutes": int(duration.total_seconds() // 60),
        "attendees": attendees,
        "checked_candidates": checked,
        "search_until": search_end.isoformat(),
        "availability_source": source,
    }


def find_nearest_slot(
    *,
    config: OutlookConfig,
    attendees: list[str],
    preferred: datetime,
    duration: timedelta,
    max_days: int,
    step: timedelta,
    max_items: int,
    source: AvailabilitySource = "freebusy",
    workers: int = 4,
    verify_calendar: bool = True,
) -> dict[str, Any]:
    if not attendees:
        raise ValueError("Укажите хотя бы одного участника (--attendee).")
    if duration <= timedelta(0):
        raise ValueError("Длительность должна быть больше 0.")
    if max_days < 1:
        raise ValueError("--max-days должно быть >= 1.")

    with timed_step("align.preferred"):
        requested = to_local(preferred, config).replace(second=0, microsecond=0)
        search_start = align_preferred(requested, config)
        earliest_allowed = max(requested, search_start, not_before_now(config))
    search_end = earliest_allowed + timedelta(days=max_days)
    logger.info(
        "Поиск: requested=%s, earliest=%s, until=%s, attendees=%d, step=%s, duration=%s, source=%s",
        requested.isoformat(),
        earliest_allowed.isoformat(),
        search_end.isoformat(),
        len(attendees),
        step,
        duration,
        source,
    )

    logger.info(
        "Загрузка занятости (%d участников, %d дн., метод=%s) ...",
        len(attendees),
        max_days,
        source,
    )
    busy_by_attendee = fetch_all_busy_intervals(
        config,
        attendees,
        earliest_allowed,
        search_end,
        source=source,
        max_items=max_items,
        workers=workers,
    )

    union_busy = union_busy_for_all(
        busy_by_attendee,
        config,
        earliest_allowed,
        search_end,
    )
    logger.info(
        "Объединённая занятость: %d блоков (если 0 — все свободны в диапазоне)",
        len(union_busy),
    )

    checked = 0
    union_busy_search = list(union_busy)
    max_calendar_verifications = max(
        500,
        int((search_end - earliest_allowed).total_seconds() // max(step.total_seconds(), 60)) + 10,
    )
    verification_attempts = 0
    with timed_step("scan.slots", max_days=max_days, step_minutes=int(step.total_seconds() // 60)):
        while True:
            slot, step_checked = find_slot_via_busy_gaps(
                earliest_allowed=earliest_allowed,
                search_end=search_end,
                duration=duration,
                step=step,
                union_busy=union_busy_search,
                config=config,
            )
            checked += step_checked
            if slot is None:
                break
            if slot < earliest_allowed:
                logger.warning(
                    "Пропуск слота раньше earliest_allowed: %s < %s",
                    slot.isoformat(),
                    earliest_allowed.isoformat(),
                )
                union_busy_search = coalesce_intervals(
                    union_busy_search + [(slot, slot + duration)],
                    config,
                    clip_start=earliest_allowed,
                    clip_end=search_end,
                )
                verification_attempts += 1
                if verification_attempts >= max_calendar_verifications:
                    break
                continue
            if not verify_calendar:
                logger.info("Слот найден после %d проверок (free/busy, по промежуткам)", checked)
                return _slot_search_result(
                    requested=requested,
                    earliest_allowed=earliest_allowed,
                    candidate=slot,
                    duration=duration,
                    attendees=attendees,
                    checked=checked,
                    search_end=search_end,
                    source=source,
                )
            calendar_ok, _calendar_busy = verify_slot_with_calendar(
                config=config,
                attendees=attendees,
                slot_start=slot,
                duration=duration,
                max_items=max_items,
                workers=workers,
            )
            if calendar_ok:
                logger.info("Слот найден после %d проверок (free/busy + events)", checked)
                return _slot_search_result(
                    requested=requested,
                    earliest_allowed=earliest_allowed,
                    candidate=slot,
                    duration=duration,
                    attendees=attendees,
                    checked=checked,
                    search_end=search_end,
                    source=source,
                )
            logger.info(
                "Слот %s свободен по merged, но занят по free/busy events — ищем следующий",
                slot.isoformat(),
            )
            union_busy_search = coalesce_intervals(
                union_busy_search + [(slot, slot + duration)],
                config,
                clip_start=earliest_allowed,
                clip_end=search_end,
            )
            verification_attempts += 1
            if verification_attempts >= max_calendar_verifications:
                logger.warning(
                    "Достигнут лимит проверок calendar_events=%d",
                    max_calendar_verifications,
                )
                break

    busy_summary = ", ".join(
        f"{email}: {len(intervals)} интервалов"
        for email, intervals in busy_by_attendee.items()
    )
    logger.info(
        "Слот не найден: проверено=%d; объединённых блоков занятости=%d; %s",
        checked,
        len(union_busy),
        busy_summary,
    )
    raise RuntimeError(
        f"Свободный слот не найден в течение {max_days} дн. от {earliest_allowed.isoformat()}."
    )


def format_slot(result: dict[str, Any]) -> str:
    start = datetime.fromisoformat(result["slot_start"])
    end = datetime.fromisoformat(result["slot_end"])
    preferred = datetime.fromisoformat(result["preferred"])
    duration = result["duration_minutes"]
    lines = [
        f"Желаемая дата (отсчёт поиска): {preferred.strftime('%d.%m.%Y %H:%M')}",
        f"Ближайший свободный слот: {start.strftime('%d.%m.%Y %H:%M')} — "
        f"{end.strftime('%H:%M')} ({duration} мин)",
        "Участники:",
    ]
    for email in result["attendees"]:
        lines.append(f"  - {email}")

    rooms_status = result.get("rooms_status") or []
    if rooms_status:
        lines.append("")
        lines.append(format_rooms_status(rooms_status, slot_start=start, slot_end=end))
    return "\n".join(lines)


def attach_room_status(
    result: dict[str, Any],
    *,
    config: OutlookConfig,
    rooms_file: str | None,
    skip_rooms: bool,
) -> dict[str, Any]:
    if skip_rooms:
        return result

    rooms = load_rooms(rooms_file)
    if not rooms:
        logger.info("Переговорные: файл %s пуст или не найден", rooms_file or DEFAULT_ROOMS_FILE)
        return result

    slot_start = datetime.fromisoformat(result["slot_start"])
    slot_end = datetime.fromisoformat(result["slot_end"])
    with timed_step("rooms.check", rooms=len(rooms)):
        result["rooms_status"] = check_rooms_status(
            config=config,
            rooms=rooms,
            slot_start=slot_start,
            slot_end=slot_end,
        )
    free = sum(1 for row in result["rooms_status"] if row["status"] == "free")
    logger.info("Переговорные: свободно %d из %d", free, len(result["rooms_status"]))
    return result


def dispatch_find_quorum_meeting_slots(
    *,
    attendees: list[str],
    preferred: str,
    duration_minutes: int,
    required_attendees: list[str] | None = None,
    attendee_weights: dict[str, float] | None = None,
    min_coverage_ratio: float = 0.7,
    max_results: int = 3,
    verify_top_n: int = 3,
    max_days: int = 30,
    step_minutes: int = 15,
    max_items: int = 500,
    source: AvailabilitySource = "freebusy",
    workers: int = 4,
    timezone: str | None = None,
    verify_calendar: bool = True,
    quiet: bool = True,
    include_timing: bool = False,
    config: OutlookConfig | None = None,
) -> dict[str, Any]:
    """Ищет слоты для большинства участников и возвращает конфликты для перепланирования."""
    config = config or load_config()
    attendee_list = [email.strip() for email in attendees if email.strip()]
    if not attendee_list:
        raise ValueError("Укажите хотя бы одного участника (attendees).")

    required_list = [email.strip() for email in (required_attendees or []) if email.strip()]
    reset_timing_report()
    setup_logging(quiet=quiet)

    tz_name = timezone or config.timezone
    preferred_dt = parse_start(preferred, tz_name)
    result = find_quorum_slots(
        config=config,
        attendees=attendee_list,
        required_attendees=required_list or None,
        attendee_weights=attendee_weights,
        preferred=preferred_dt,
        duration=timedelta(minutes=duration_minutes),
        max_days=max_days,
        step=timedelta(minutes=max(step_minutes, 1)),
        max_items=max_items,
        source=source,
        workers=max(workers, 1),
        min_coverage_ratio=min_coverage_ratio,
        max_results=max(max_results, 1),
        verify_top_n=max(verify_top_n, 0),
        verify_calendar=verify_calendar,
    )
    if include_timing:
        log_timing_summary()
        result["timing_ms"] = list(_timing_report)
    return result


def dispatch_find_meeting_slot(
    *,
    attendees: list[str],
    preferred: str,
    duration_minutes: int,
    max_days: int = 30,
    step_minutes: int = 15,
    max_items: int = 500,
    source: AvailabilitySource = "freebusy",
    workers: int = 4,
    timezone: str | None = None,
    rooms_file: str | None = None,
    skip_rooms: bool = False,
    verify_calendar: bool = False,
    quiet: bool = True,
    include_timing: bool = False,
    config: OutlookConfig | None = None,
) -> dict[str, Any]:
    """Ищет ближайший свободный слот и возвращает JSON для API/агента."""
    config = config or load_config()
    attendee_list = [email.strip() for email in attendees if email.strip()]
    if not attendee_list:
        raise ValueError("Укажите хотя бы одного участника (attendees).")

    reset_timing_report()
    setup_logging(quiet=quiet)

    tz_name = timezone or config.timezone
    preferred_dt = parse_start(preferred, tz_name)
    result = find_nearest_slot(
        config=config,
        attendees=attendee_list,
        preferred=preferred_dt,
        duration=timedelta(minutes=duration_minutes),
        max_days=max_days,
        step=timedelta(minutes=max(step_minutes, 1)),
        max_items=max_items,
        source=source,
        workers=max(workers, 1),
        verify_calendar=verify_calendar,
    )
    result = attach_room_status(
        result,
        config=config,
        rooms_file=rooms_file,
        skip_rooms=skip_rooms,
    )
    if include_timing:
        log_timing_summary()
        result["timing_ms"] = list(_timing_report)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Найти ближайший свободный слот для совещания у нескольких участников."
    )
    parser.add_argument(
        "--attendee",
        action="append",
        default=[],
        metavar="EMAIL",
        help="E-mail участника (можно указать несколько раз)",
    )
    parser.add_argument(
        "--preferred",
        required=True,
        help="Желаемая дата/время начала поиска (не обязательно свободна)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        required=True,
        metavar="MIN",
        help="Длительность совещания в минутах",
    )
    parser.add_argument(
        "--max-days",
        type=int,
        default=30,
        help="Сколько дней вперёд искать (по умолчанию 30)",
    )
    parser.add_argument(
        "--step",
        type=int,
        default=15,
        help="Шаг перебора слотов в минутах (по умолчанию 15)",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=500,
        help="Максимум событий календаря на участника (--source calendar)",
    )
    parser.add_argument(
        "--source",
        choices=("freebusy", "calendar"),
        default="freebusy",
        help="freebusy — GetUserAvailability (быстро, по умолчанию); "
        "calendar — calendar.view (медленнее, запасной вариант)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Потоки для --source calendar (по умолчанию 4)",
    )
    parser.add_argument(
        "--tz",
        default=None,
        help="Часовой пояс для --preferred (по умолчанию OUTLOOK_TIMEZONE из .env)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Сохранить результат в JSON",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Не выводить логи производительности",
    )
    parser.add_argument(
        "--rooms-file",
        default=str(DEFAULT_ROOMS_FILE),
        help="JSON со списком переговорных (проверка занятости на найденный слот)",
    )
    parser.add_argument(
        "--no-rooms",
        action="store_true",
        help="Не проверять переговорные",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    config = load_config()

    try:
        result = dispatch_find_meeting_slot(
            attendees=[email.strip() for email in args.attendee if email.strip()],
            preferred=args.preferred,
            duration_minutes=args.duration,
            max_days=args.max_days,
            step_minutes=max(args.step, 1),
            max_items=args.max_items,
            source=args.source,
            workers=max(args.workers, 1),
            timezone=args.tz,
            rooms_file=args.rooms_file.strip() or None,
            skip_rooms=args.no_rooms,
            quiet=args.quiet,
            include_timing=True,
            config=config,
        )
    except Exception as error:
        log_timing_summary()
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1

    log_timing_summary()
    print(format_slot(result))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as file:
            json.dump(result, file, ensure_ascii=False, indent=2)
        print(f"\nСохранено: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
