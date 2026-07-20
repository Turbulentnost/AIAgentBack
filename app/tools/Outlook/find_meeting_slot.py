"""
Поиск ближайшего свободного слота для совещания у нескольких участников (EWS).

Правила:
  - только рабочие дни (пн–пт);
  - время 08:00–17:00 (совещание должно полностью уложиться);
  - запрещены пересечения с 10:00–10:15, 12:00–13:00, 15:00–15:15.

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
from app.tools.Outlook.read_calendars import connect_as_owner
from app.tools.Outlook.send_meeting_invite import connect_account, load_config, parse_start

AvailabilitySource = Literal["freebusy", "calendar"]

WORK_START = dt_time(8, 0)
WORK_END = dt_time(17, 0)
FORBIDDEN_BLOCKS = (
    (dt_time(10, 0), dt_time(10, 15)),
    (dt_time(12, 0), dt_time(13, 0)),
    (dt_time(15, 0), dt_time(15, 15)),
)
BUSY_STATUSES = frozenset({"Busy", "Tentative", "OOF", "WorkingElsewhere"})
MERGED_BUSY_CHARS = frozenset("23")

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
    candidate = combine(dt, WORK_START, config) + timedelta(days=1)
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
        return next_workday_start(current - timedelta(days=1), config)
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
) -> list[tuple[datetime, datetime]]:
    if not merged:
        return []

    local_start = to_local(range_start, config).replace(second=0, microsecond=0)
    local_end = to_local(range_end, config).replace(second=0, microsecond=0)
    total_seconds = (local_end - local_start).total_seconds()
    if total_seconds <= 0:
        return []

    slot_seconds = total_seconds / len(merged)
    intervals: list[tuple[datetime, datetime]] = []
    busy_start: datetime | None = None

    for index, char in enumerate(merged):
        slot_start = local_start + timedelta(seconds=slot_seconds * index)
        if char not in MERGED_BUSY_CHARS:
            if busy_start is not None:
                intervals.append((busy_start, slot_start))
                busy_start = None
            continue
        if busy_start is None:
            busy_start = slot_start

    if busy_start is not None:
        intervals.append((busy_start, local_end))
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


def calendar_events_from_freebusy_view(view: Any, attendee: str) -> list[Any]:
    events = getattr(view, "calendar_events", None)
    if events is not None:
        return list(events or [])
    merged = getattr(view, "merged", None)
    if isinstance(merged, str) and merged and all(char not in MERGED_BUSY_CHARS for char in merged):
        return []
    message = getattr(view, "message", None) or getattr(view, "view_type", None) or "FreeBusyView"
    raise RuntimeError(f"Exchange не вернул занятость для {attendee}: {message}")


def fetch_busy_intervals_freebusy(
    config: OutlookConfig,
    attendees: list[str],
    range_start: datetime,
    range_end: datetime,
) -> dict[str, list[tuple[datetime, datetime]]]:
    """GetUserAvailability — один запрос на всех участников (быстро)."""
    attendees = [email.strip() for email in attendees]
    with timed_step("ews.connect.service"):
        service_account = connect_account(config)

    mailbox_data = [(email, "Required", False) for email in attendees]
    with timed_step("ews.freebusy.get", attendees=len(attendees)):
        views = list(
            service_account.protocol.get_free_busy_info(
                mailbox_data,
                start=to_ews(range_start, config),
                end=to_ews(range_end, config),
                requested_view="DetailedMerged",
            )
        )

    if len(views) != len(attendees):
        raise RuntimeError(
            f"Free/busy вернул {len(views)} ответов для {len(attendees)} участников"
        )

    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]] = {}
    for email, view in zip(attendees, views):
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
            sum(1 for char in merged if char in MERGED_BUSY_CHARS)
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


def is_free_for_all(
    start: datetime,
    duration: timedelta,
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    config: OutlookConfig,
) -> bool:
    local_start = to_local(start, config)
    local_end = local_start + duration
    for intervals in busy_by_attendee.values():
        for busy_start, busy_end in intervals:
            if intervals_overlap(
                local_start,
                local_end,
                to_local(busy_start, config),
                to_local(busy_end, config),
            ):
                return False
    return True


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
            current = next_workday_start(current - timedelta(days=1), config)
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
    window_start = slot_start - timedelta(hours=2)
    window_end = slot_start + duration + timedelta(hours=2)
    calendar_busy = fetch_all_busy_intervals(
        config,
        attendees,
        window_start,
        window_end,
        source="calendar",
        max_items=max_items,
        workers=workers,
    )
    return is_free_for_all(slot_start, duration, calendar_busy, config), calendar_busy


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
    verify_calendar: bool = False,
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
    with timed_step("scan.slots", max_days=max_days, step_minutes=int(step.total_seconds() // 60)):
        slot, checked = find_slot_via_busy_gaps(
            earliest_allowed=earliest_allowed,
            search_end=search_end,
            duration=duration,
            step=step,
            union_busy=union_busy,
            config=config,
        )
        if slot is not None:
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
            calendar_ok, calendar_busy = verify_slot_with_calendar(
                config=config,
                attendees=attendees,
                slot_start=slot,
                duration=duration,
                max_items=max_items,
                workers=workers,
            )
            if calendar_ok:
                logger.info("Слот найден после %d проверок", checked)
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
                "Слот %s свободен по free/busy, но занят по calendar — повторный поиск не выполняется",
                slot.isoformat(),
            )

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
