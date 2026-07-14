from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from typing import Any

from app.services.meeting_constants import COMPANY_CALENDAR_SLOT_MAX_ITEMS, RESCHEDULE_HINT_SEARCH_DAYS
from app.tools.Outlook.meeting_rooms import (
    DEFAULT_ROOMS_FILE,
    check_rooms_status,
    format_rooms_status,
    load_rooms,
)
from app.tools.Outlook.outlook_config import OutlookConfig
from app.tools.Outlook.read_calendars import read_calendar_items_in_range
from app.tools.Outlook.send_meeting_invite import load_config, parse_start

from .availability import is_free_for_attendee
from .busy import (
    fetch_all_busy_intervals,
    busy_intervals_and_events_from_freebusy,
    fetch_freebusy_calendar_events,
)
from .constants import AvailabilitySource
from .conflicts import (
    attach_reschedule_hints,
    build_conflict_records,
    conflicting_calendar_items_at_slot,
    conflicting_company_calendar_items_at_slot,
    conflicting_events_at_slot,
    conflicting_intervals_at_slot,
    dedupe_conflict_records,
    hydrate_company_calendar_items_for_slot,
    movability_reason,
)
from .search import find_nearest_slot, find_quorum_slots
from .timing import get_timing_report, logger, log_timing_summary, reset_timing_report, setup_logging, timed_step


def _cached_blocking_events(
    records: list[dict[str, Any]],
    *,
    email: str,
) -> list[dict[str, Any]]:
    """Детали конфликта из кэша без подсказок переноса (ручное планирование)."""
    events: list[dict[str, Any]] = []
    for record in records:
        source = record.get("source") or "interval"
        if source not in {"calendar", "freebusy", "interval", "company_calendar"}:
            source = "interval"
        events.append(
            {
                **record,
                "source": source,
                "movability": record.get("movability") or "medium",
                "movability_reason": movability_reason(
                    busy_type=str(record.get("busy_type") or ""),
                    subject=str(record.get("event_subject") or ""),
                    source=source,
                ),
                "can_auto_reschedule": False,
                "reschedule_hint_start": None,
                "reschedule_hint_end": None,
                "email": email,
            }
        )
    return events


def _busy_intervals_for_email(
    email: str,
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
) -> list[tuple[datetime, datetime]]:
    normalized = email.strip().lower()
    return busy_by_attendee.get(normalized, busy_by_attendee.get(email, []))


def _busy_attendee_emails(
    *,
    attendee_emails: list[str],
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    slot_start: datetime,
    duration: timedelta,
    config: OutlookConfig,
) -> list[str]:
    return [
        email
        for email in attendee_emails
        if not is_free_for_attendee(
            slot_start,
            duration,
            _busy_intervals_for_email(email, busy_by_attendee),
            config,
        )
    ]


def _company_calendar_query_range(
    *,
    slot_start: datetime,
    slot_end: datetime,
) -> tuple[datetime, datetime]:
    """Окно чтения общего календаря: только выбранный слот ± запас.

    Нельзя расширять до busy-блока из freebusy-кэша: там часто 08:00–17:00,
    и EWS читает сотни событий за весь день вместо 30-минутного слота.
    """
    from app.services.meeting_constants import COMPANY_CALENDAR_SLOT_PAD_MINUTES

    pad = timedelta(minutes=COMPANY_CALENDAR_SLOT_PAD_MINUTES)
    return slot_start - pad, slot_end + pad


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
    include_company_calendar: bool = False,
    light_reschedule_hints: bool = False,
    cached_busy_by_attendee: dict[str, list[tuple[datetime, datetime]]] | None = None,
) -> dict[str, Any]:
    """Статус каждого участника в выбранном слоте: свободен/занят и мешающие встречи.

    Ручное планирование (cached_busy_by_attendee задан):
    1. Кэш free/busy — источник занят/свободен на слоте.
    2. Все свободны по кэшу — без запросов в Exchange.
    3. Занятые по кэшу — общий календарь на слот ±15 мин для деталей встречи.
       Участник свободен только если календарь успешно прочитан, на слоте есть
       события, но участник не указан ни в одном; при ошибке чтения — занят по кэшу.
    """
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
        slot_end + timedelta(days=RESCHEDULE_HINT_SEARCH_DAYS),
        slot_start + timedelta(days=30),
    )

    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]] = {}
    conflict_events: dict[str, list[Any]] = {}
    if attendee_emails:
        if cached_busy_by_attendee is not None:
            busy_by_attendee = cached_busy_by_attendee
            logger.info(
                "slot_details.reuse_cached_busy attendees=%d",
                len(attendee_emails),
            )
        else:
            busy_by_attendee, conflict_events = busy_intervals_and_events_from_freebusy(
                config,
                attendee_emails,
                window_start,
                window_end,
                max_items=max_items,
            )

    use_cached_busy_only = cached_busy_by_attendee is not None
    busy_attendee_emails = _busy_attendee_emails(
        attendee_emails=attendee_emails,
        busy_by_attendee=busy_by_attendee,
        slot_start=slot_start,
        duration=duration,
        config=config,
    )

    company_calendar_items: list[Any] = []
    company_calendar_loaded = False
    if include_company_calendar and busy_attendee_emails:
        company_calendar = (config.company_calendar or "").strip()
        if company_calendar:
            calendar_range_start, calendar_range_end = _company_calendar_query_range(
                slot_start=slot_start,
                slot_end=slot_end,
            )
            calendar_max_items = min(max_calendar_items, COMPANY_CALENDAR_SLOT_MAX_ITEMS)
            range_minutes = int(
                (calendar_range_end - calendar_range_start).total_seconds() // 60
            )
            logger.info(
                "slot_details.company_calendar_for_busy_slot attendees=%d busy=%d "
                "range_start=%s range_end=%s range_minutes=%d cached=%s",
                len(attendee_emails),
                len(busy_attendee_emails),
                calendar_range_start.isoformat(),
                calendar_range_end.isoformat(),
                range_minutes,
                use_cached_busy_only,
            )
            try:
                company_calendar_items = read_calendar_items_in_range(
                    config,
                    company_calendar,
                    range_start=calendar_range_start,
                    range_end=calendar_range_end,
                    max_items=calendar_max_items,
                    load_attendees=False,
                )
                hydrated_count = hydrate_company_calendar_items_for_slot(
                    company_calendar_items,
                    slot_start=slot_start,
                    slot_end=slot_end,
                    config=config,
                )
                company_calendar_loaded = True
                logger.info(
                    "slot_details.company_calendar_read items=%d hydrated=%d",
                    len(company_calendar_items),
                    hydrated_count,
                )
            except Exception as exc:
                company_calendar_loaded = False
                logger.warning(
                    "company_calendar_read_failed calendar=%s error=%s",
                    company_calendar,
                    exc,
                )
    elif use_cached_busy_only:
        logger.info(
            "slot_details.cached_all_free attendees=%d",
            len(attendee_emails),
        )

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

        busy_intervals = _busy_intervals_for_email(email, busy_by_attendee)
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

        if use_cached_busy_only and include_company_calendar:
            company_records: list[dict[str, Any]] = []
            if company_calendar_items:
                company_records = conflicting_company_calendar_items_at_slot(
                    company_calendar_items,
                    slot_start,
                    duration,
                    config,
                    attendee_email=email,
                    attendee_fio=fio,
                )
            if (
                company_calendar_loaded
                and company_calendar_items
                and not company_records
            ):
                logger.info(
                    "slot_details.cache_busy_treated_free email=%s company_items=%d",
                    email,
                    len(company_calendar_items),
                )
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

            if not company_records:
                interval_records = conflicting_intervals_at_slot(
                    busy_intervals,
                    slot_start,
                    duration,
                    config,
                )
                for record in interval_records:
                    record["source"] = "interval"
                blocking_events = _cached_blocking_events(interval_records, email=email)
                if not company_calendar_loaded:
                    logger.info(
                        "slot_details.cache_busy_without_company_calendar email=%s",
                        email,
                    )
                participants.append(
                    {
                        "fio": fio,
                        "email": email,
                        "role": role,
                        "status": "busy",
                        "blocking_events": blocking_events,
                        "calendar_access_error": None,
                    }
                )
                continue

            if light_reschedule_hints:
                blocking_events = attach_reschedule_hints(
                    company_records,
                    owner_email=email,
                    busy_intervals=busy_intervals,
                    config=config,
                    step=step,
                    search_end=hint_search_end,
                    reserved_slot=(slot_start, slot_end),
                    light_hints=False,
                )
                for event in blocking_events:
                    event["email"] = email
            else:
                blocking_events = _cached_blocking_events(company_records, email=email)

            participants.append(
                {
                    "fio": fio,
                    "email": email,
                    "role": role,
                    "status": "busy",
                    "blocking_events": blocking_events,
                    "calendar_access_error": None,
                }
            )
            continue

        calendar_error: str | None = None
        calendar_records: list[dict[str, Any]] = []
        if not include_company_calendar:
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

        company_records: list[dict[str, Any]] = []
        if company_calendar_items:
            company_records = conflicting_company_calendar_items_at_slot(
                company_calendar_items,
                slot_start,
                duration,
                config,
                attendee_email=email,
                attendee_fio=fio,
            )

        interval_records: list[dict[str, Any]] = []
        if not calendar_records and not freebusy_records and not company_records:
            interval_records = conflicting_intervals_at_slot(
                busy_intervals,
                slot_start,
                duration,
                config,
            )
            for record in interval_records:
                record["source"] = "interval"

        merged_records = dedupe_conflict_records(
            calendar_records + freebusy_records + company_records + interval_records
        )
        if company_records and any(
            str(record.get("event_subject") or "").strip() for record in merged_records
        ):
            calendar_error = None

        if use_cached_busy_only:
            # Ручное планирование: без подсказок переноса, только факты конфликта.
            blocking_events = _cached_blocking_events(merged_records, email=email)
        else:
            blocking_events = attach_reschedule_hints(
                merged_records,
                owner_email=email,
                busy_intervals=busy_intervals,
                config=config,
                step=step,
                search_end=hint_search_end,
                reserved_slot=(slot_start, slot_end),
                light_hints=light_reschedule_hints,
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
    latest_allowed: str | None = None,
    raise_if_empty: bool = True,
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
    latest_dt = parse_start(latest_allowed, tz_name) if latest_allowed else None
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
        latest_allowed=latest_dt,
        raise_if_empty=raise_if_empty,
    )
    if include_timing:
        log_timing_summary()
        result["timing_ms"] = get_timing_report()
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
        result["timing_ms"] = get_timing_report()
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

