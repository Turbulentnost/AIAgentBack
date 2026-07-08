from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from typing import Any

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
from .busy import fetch_all_busy_intervals, fetch_freebusy_calendar_events
from .constants import AvailabilitySource
from .conflicts import (
    attach_reschedule_hints,
    build_conflict_records,
    conflicting_calendar_items_at_slot,
    conflicting_events_at_slot,
    conflicting_intervals_at_slot,
    dedupe_conflict_records,
)
from .search import find_nearest_slot, find_quorum_slots
from .timing import logger, log_timing_summary, reset_timing_report, setup_logging, timed_step


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

